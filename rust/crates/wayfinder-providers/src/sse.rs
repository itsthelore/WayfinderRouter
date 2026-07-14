//! Incremental, bounded Server-Sent Events decoding.
//!
//! The decoder accepts arbitrary transport fragmentation without retaining an
//! unbounded line or event. It performs no I/O and is suitable for both
//! OpenAI-compatible relays and the Anthropic translation state machine.

use std::str;

use thiserror::Error;

/// Default maximum bytes in one SSE field line (256 KiB).
pub const DEFAULT_MAX_LINE_BYTES: usize = 256 * 1_024;
/// Default maximum aggregate `data` bytes in one SSE event (1 MiB).
pub const DEFAULT_MAX_EVENT_BYTES: usize = 1_024 * 1_024;

/// One decoded SSE event.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SseEvent {
    /// Explicit `event:` value, or `message` by default.
    pub event: String,
    /// All `data:` fields joined with a newline as required by SSE.
    pub data: String,
    /// Optional `id:` field.
    pub id: Option<String>,
}

/// Malformed or over-limit upstream streaming data.
#[derive(Clone, Debug, Error, PartialEq, Eq)]
pub enum SseDecodeError {
    /// A single field line exceeded the configured bound.
    #[error("SSE line exceeds {limit} bytes")]
    LineTooLarge { limit: usize },
    /// Accumulated data fields exceeded the configured event bound.
    #[error("SSE event exceeds {limit} data bytes")]
    EventTooLarge { limit: usize },
    /// Provider SSE fields must be valid UTF-8.
    #[error("SSE field is not valid UTF-8")]
    InvalidUtf8,
    /// Bounds must be non-zero.
    #[error("SSE limits must be positive")]
    InvalidLimits,
}

/// Stateful incremental SSE decoder.
#[derive(Clone, Debug)]
pub struct SseDecoder {
    max_line_bytes: usize,
    max_event_bytes: usize,
    line: Vec<u8>,
    event_type: Option<String>,
    data: String,
    has_data: bool,
    data_bytes: usize,
    id: Option<String>,
}

impl SseDecoder {
    /// Construct a decoder with explicit memory bounds.
    pub fn new(max_line_bytes: usize, max_event_bytes: usize) -> Result<Self, SseDecodeError> {
        if max_line_bytes == 0 || max_event_bytes == 0 {
            return Err(SseDecodeError::InvalidLimits);
        }
        Ok(Self {
            max_line_bytes,
            max_event_bytes,
            line: Vec::new(),
            event_type: None,
            data: String::new(),
            has_data: false,
            data_bytes: 0,
            id: None,
        })
    }

    /// Feed one arbitrary byte fragment and return every completed event.
    pub fn push(&mut self, chunk: &[u8]) -> Result<Vec<SseEvent>, SseDecodeError> {
        let mut events = Vec::new();
        for byte in chunk {
            if *byte == b'\n' {
                let mut line = std::mem::take(&mut self.line);
                if line.last() == Some(&b'\r') {
                    let _ = line.pop();
                }
                self.process_line(&line, &mut events)?;
            } else {
                if self.line.len() >= self.max_line_bytes {
                    return Err(SseDecodeError::LineTooLarge {
                        limit: self.max_line_bytes,
                    });
                }
                self.line.push(*byte);
            }
        }
        Ok(events)
    }

    /// Finish a transport that closed without a trailing blank line.
    pub fn finish(&mut self) -> Result<Vec<SseEvent>, SseDecodeError> {
        let mut events = Vec::new();
        if !self.line.is_empty() {
            let mut line = std::mem::take(&mut self.line);
            if line.last() == Some(&b'\r') {
                let _ = line.pop();
            }
            self.process_line(&line, &mut events)?;
        }
        if let Some(event) = self.dispatch() {
            events.push(event);
        }
        Ok(events)
    }

    fn process_line(
        &mut self,
        raw_line: &[u8],
        events: &mut Vec<SseEvent>,
    ) -> Result<(), SseDecodeError> {
        if raw_line.is_empty() {
            if let Some(event) = self.dispatch() {
                events.push(event);
            }
            return Ok(());
        }
        let line = str::from_utf8(raw_line).map_err(|_| SseDecodeError::InvalidUtf8)?;
        if line.starts_with(':') {
            return Ok(());
        }
        let (field, value) = line.split_once(':').map_or((line, ""), |(field, value)| {
            (field, value.strip_prefix(' ').unwrap_or(value))
        });
        match field {
            "event" => self.event_type = Some(value.to_owned()),
            "data" => {
                let separator = usize::from(self.has_data);
                let new_size = self
                    .data_bytes
                    .saturating_add(separator)
                    .saturating_add(value.len());
                if new_size > self.max_event_bytes {
                    return Err(SseDecodeError::EventTooLarge {
                        limit: self.max_event_bytes,
                    });
                }
                self.data_bytes = new_size;
                if self.has_data {
                    self.data.push('\n');
                }
                self.data.push_str(value);
                self.has_data = true;
            }
            "id" if !value.contains('\0') => self.id = Some(value.to_owned()),
            _ => {}
        }
        Ok(())
    }

    fn dispatch(&mut self) -> Option<SseEvent> {
        let event_type = self.event_type.take();
        let data = std::mem::take(&mut self.data);
        let had_data = std::mem::replace(&mut self.has_data, false);
        self.data_bytes = 0;
        let id = self.id.clone();
        if !had_data {
            return None;
        }
        Some(SseEvent {
            event: event_type.unwrap_or_else(|| "message".to_owned()),
            data,
            id,
        })
    }
}

impl Default for SseDecoder {
    fn default() -> Self {
        Self {
            max_line_bytes: DEFAULT_MAX_LINE_BYTES,
            max_event_bytes: DEFAULT_MAX_EVENT_BYTES,
            line: Vec::new(),
            event_type: None,
            data: String::new(),
            has_data: false,
            data_bytes: 0,
            id: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn arbitrary_fragmentation_reassembles_one_event() -> Result<(), SseDecodeError> {
        let mut decoder = SseDecoder::default();
        assert!(decoder.push(b"eve")?.is_empty());
        assert!(decoder.push(b"nt: delta\r\nda")?.is_empty());
        assert!(decoder.push(b"ta: {\"x\":")?.is_empty());
        assert_eq!(
            decoder.push(b"1}\r\nid: 7\r\n\r\n")?,
            [SseEvent {
                event: "delta".to_owned(),
                data: "{\"x\":1}".to_owned(),
                id: Some("7".to_owned()),
            }]
        );
        Ok(())
    }

    #[test]
    fn repeated_data_lines_join_with_newline() -> Result<(), SseDecodeError> {
        let mut decoder = SseDecoder::default();
        assert_eq!(
            decoder.push(b"data: first\ndata:second\n\n")?,
            [SseEvent {
                event: "message".to_owned(),
                data: "first\nsecond".to_owned(),
                id: None,
            }]
        );
        Ok(())
    }

    #[test]
    fn comments_unknown_fields_and_empty_events_are_ignored() -> Result<(), SseDecodeError> {
        let mut decoder = SseDecoder::default();
        assert_eq!(
            decoder
                .push(b": keepalive\nretry: 1000\nevent: ignored-without-data\n\ndata: ok\n\n")?,
            [SseEvent {
                event: "message".to_owned(),
                data: "ok".to_owned(),
                id: None,
            }]
        );
        Ok(())
    }

    #[test]
    fn last_event_id_persists_across_events() -> Result<(), SseDecodeError> {
        let mut decoder = SseDecoder::default();
        let events = decoder.push(b"id: 9\ndata: one\n\ndata: two\n\n")?;
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].id.as_deref(), Some("9"));
        assert_eq!(events[1].id.as_deref(), Some("9"));
        Ok(())
    }

    #[test]
    fn finish_dispatches_unterminated_final_data() -> Result<(), SseDecodeError> {
        let mut decoder = SseDecoder::default();
        assert!(decoder.push(b"data: [DONE]")?.is_empty());
        assert_eq!(decoder.finish()?[0].data, "[DONE]");
        Ok(())
    }

    #[test]
    fn line_bound_is_enforced_before_growth() -> Result<(), SseDecodeError> {
        let mut decoder = SseDecoder::new(4, 10)?;
        assert_eq!(
            decoder.push(b"data:").err(),
            Some(SseDecodeError::LineTooLarge { limit: 4 })
        );
        Ok(())
    }

    #[test]
    fn event_bound_includes_joining_newlines() -> Result<(), SseDecodeError> {
        let mut decoder = SseDecoder::new(20, 2)?;
        assert!(decoder.push(b"data:a\n")?.is_empty());
        assert_eq!(
            decoder.push(b"data:b\n").err(),
            Some(SseDecodeError::EventTooLarge { limit: 2 })
        );
        Ok(())
    }

    #[test]
    fn invalid_utf8_and_zero_limits_are_rejected() {
        assert_eq!(
            SseDecoder::new(0, 1).err(),
            Some(SseDecodeError::InvalidLimits)
        );
        let mut decoder = SseDecoder::default();
        assert_eq!(
            decoder
                .push(&[b'd', b'a', b't', b'a', b':', 0xff, b'\n'])
                .err(),
            Some(SseDecodeError::InvalidUtf8)
        );
    }
}
