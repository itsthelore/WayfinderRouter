import Darwin
import Foundation

public struct BoundedProcessResult: Sendable {
    public let exitCode: Int32
    public let stdout: Data
    public let stderr: Data

    public init(exitCode: Int32, stdout: Data = Data(), stderr: Data = Data()) {
        self.exitCode = exitCode
        self.stdout = stdout
        self.stderr = stderr
    }
}

public enum BoundedProcessError: Error, Equatable, LocalizedError, Sendable {
    case invalidLimit
    case inputLimitExceeded
    case launchFailed
    case timedOut
    case outputLimitExceeded

    public var errorDescription: String? {
        switch self {
        case .invalidLimit:
            "The helper process limit is invalid."
        case .inputLimitExceeded:
            "The helper input exceeded its allowed size."
        case .launchFailed:
            "The bundled helper could not be started."
        case .timedOut:
            "The bundled helper did not finish in time."
        case .outputLimitExceeded:
            "The bundled helper returned too much output."
        }
    }
}

/// Executes one already-resolved executable with hard time and I/O bounds.
///
/// The caller is responsible for authenticating the executable immediately before calling this
/// runner. Output is drained concurrently so a child cannot deadlock on a full pipe.
public enum BoundedProcessRunner {
    public static let defaultEnvironment: [String: String] = [
        "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
        "LOGNAME": NSUserName(),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": NSTemporaryDirectory(),
        "USER": NSUserName(),
    ]

    public static func run(
        executable: URL,
        arguments: [String],
        stdin: Data? = nil,
        timeoutNanoseconds: UInt64,
        maximumInputBytes: Int = 64 * 1_024,
        maximumOutputBytes: Int = 64 * 1_024,
        environment: [String: String] = defaultEnvironment
    ) async throws -> BoundedProcessResult {
        guard timeoutNanoseconds > 0, maximumInputBytes > 0, maximumOutputBytes > 0 else {
            throw BoundedProcessError.invalidLimit
        }
        if let stdin, stdin.count > maximumInputBytes {
            throw BoundedProcessError.inputLimitExceeded
        }

        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        let inputPipe: Pipe?
        if stdin != nil {
            let pipe = Pipe()
            process.standardInput = pipe
            inputPipe = pipe
        } else {
            process.standardInput = FileHandle.nullDevice
            inputPipe = nil
        }

        do {
            try process.run()
        } catch {
            throw BoundedProcessError.launchFailed
        }

        // Process.run() duplicates these descriptors for the child. Closing the parent's copies
        // ensures readers observe EOF when the child exits.
        try? stdoutPipe.fileHandleForWriting.close()
        try? stderrPipe.fileHandleForWriting.close()
        try? inputPipe?.fileHandleForReading.close()

        let controller = ProcessController(process)
        let stdoutTask = Task.detached {
            readBounded(
                stdoutPipe.fileHandleForReading,
                maximumBytes: maximumOutputBytes,
                controller: controller
            )
        }
        let stderrTask = Task.detached {
            readBounded(
                stderrPipe.fileHandleForReading,
                maximumBytes: maximumOutputBytes,
                controller: controller
            )
        }
        let stdinTask = Task.detached {
            guard let stdin, let inputPipe else { return }
            try? inputPipe.fileHandleForWriting.write(contentsOf: stdin)
            try? inputPipe.fileHandleForWriting.close()
        }

        return try await withTaskCancellationHandler {
            let start = DispatchTime.now().uptimeNanoseconds
            var timedOut = false
            while controller.isRunning {
                let elapsed = DispatchTime.now().uptimeNanoseconds - start
                if elapsed >= timeoutNanoseconds {
                    timedOut = true
                    controller.requestTermination()
                    break
                }
                try Task.checkCancellation()
                try await Task.sleep(nanoseconds: min(10_000_000, timeoutNanoseconds - elapsed))
            }

            if timedOut {
                for _ in 0..<25 where controller.isRunning {
                    try? await Task.sleep(nanoseconds: 10_000_000)
                }
                controller.forceTermination()
            }

            await controller.waitUntilExit()
            _ = await stdinTask.value
            let stdout = await stdoutTask.value
            let stderr = await stderrTask.value

            if timedOut { throw BoundedProcessError.timedOut }
            if stdout.exceededLimit || stderr.exceededLimit {
                throw BoundedProcessError.outputLimitExceeded
            }
            return BoundedProcessResult(
                exitCode: controller.terminationStatus,
                stdout: stdout.data,
                stderr: stderr.data
            )
        } onCancel: {
            controller.forceTermination()
        }
    }

    private static func readBounded(
        _ handle: FileHandle,
        maximumBytes: Int,
        controller: ProcessController
    ) -> BoundedRead {
        var data = Data()
        var exceededLimit = false
        while true {
            let chunk: Data
            do {
                chunk = try handle.read(upToCount: 8 * 1_024) ?? Data()
            } catch {
                break
            }
            guard !chunk.isEmpty else { break }
            if data.count + chunk.count > maximumBytes {
                let remaining = max(0, maximumBytes - data.count)
                if remaining > 0 { data.append(chunk.prefix(remaining)) }
                exceededLimit = true
                controller.requestTermination()
            } else if !exceededLimit {
                data.append(chunk)
            }
        }
        return BoundedRead(data: data, exceededLimit: exceededLimit)
    }
}

private struct BoundedRead: Sendable {
    let data: Data
    let exceededLimit: Bool
}

private final class ProcessController: @unchecked Sendable {
    private let process: Process
    private let lock = NSLock()

    init(_ process: Process) {
        self.process = process
    }

    var isRunning: Bool {
        lock.withLock { process.isRunning }
    }

    var terminationStatus: Int32 {
        lock.withLock { process.terminationStatus }
    }

    func requestTermination() {
        lock.withLock {
            if process.isRunning { process.terminate() }
        }
    }

    func forceTermination() {
        lock.withLock {
            guard process.isRunning else { return }
            Darwin.kill(process.processIdentifier, SIGKILL)
        }
    }

    func waitUntilExit() async {
        await Task.detached { [process] in process.waitUntilExit() }.value
    }
}
