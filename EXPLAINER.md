# Wayfinder — a one-page explainer

**Wayfinder is a smart switchboard you put in front of your models.** Your app
keeps talking to "the OpenAI API" exactly as it does today — you just change one
setting (the server address) to point at Wayfinder. For every prompt, Wayfinder
looks at how heavy it is and sends the easy ones to your cheap/local model and the
hard ones to the expensive/cloud model, using **your own** API keys. You get the
same responses back, plus a note saying where each one went. Nothing about your
code changes, and you can see and tune the rule that decides.

## The mental model

It's a **proxy** — a middleman that speaks the same language as the model API your
app already uses. Like a mail-sorting room: every request still looks like a normal
API call, but on the way through, Wayfinder reads the "size" of the job and routes
it to the right desk. You don't add an SDK or rewrite anything — your existing
client just takes a `base_url`, and you point it at Wayfinder.

## What happens to a request

1. Your app sends its normal chat request to Wayfinder instead of straight to the
   provider (one config line: `base_url`).
2. Wayfinder **scores the prompt's structural complexity** — length, steps, code
   blocks, tables, and so on — with a fixed, deterministic rule. No AI is used to
   make this decision, so it is instant and free.
3. The score picks the destination: below your cut → the local/cheap model; at or
   above it → the cloud/bigger model (you can have more than two tiers).
4. Wayfinder forwards the request to that model's real endpoint **using your key**
   (read from the environment, never stored in Wayfinder) and passes the answer
   straight back.
5. The response is unchanged but carries two headers — `x-wayfinder-model` (where
   it went) and `x-wayfinder-score` (how heavy it judged the prompt) — so you can
   watch it working.

## What you set up (three things)

- **Point the client** at the gateway (`base_url`).
- **List your models**: each destination's endpoint URL, model name, and the
  *name* of the environment variable holding its key.
- **Set the cut** (a threshold) — or let Wayfinder learn one (see below).

Run it as a container next to your app; nothing else in your stack moves.

## Why it's worth it

- **Cheaper / faster** — the expensive model only handles the prompts that need it.
- **Zero code change** — it's a config switch, not a migration.
- **Transparent** — every response says where it went and why (the score).
- **Your keys, your models** — Wayfinder never holds a secret; it decides and forwards.

## It gets better with use

If a routed answer wasn't good enough, your app sends a quick thumbs-down to
Wayfinder (`POST /v1/feedback`). Those judgments accumulate, and a periodic
**recalibration** re-tunes the routing rule from them — the running gateway picks
up the new rule with no restart. So the longer you use it, the better it matches
*your* sense of "good enough." For setup, an A/B mode runs both models on sample
prompts and asks you to pick, to establish the initial cut.

## Honest limits

- It routes on **structure, not meaning** — a fast proxy for difficulty, not a
  judge of the answer. That's why the threshold is yours to calibrate; the default
  cut is a starting guess, not a guarantee.
- It only routes among **models you've configured** — it doesn't discover or host
  anything.
- It **decides and forwards; it doesn't judge quality** — the thumbs-up/down comes
  from your app or your users.
- Confirm **streaming** behaves as expected in your setup during the pilot.

## What we'd love to hear (pilots)

A few quick signals are plenty:

- **Did the routing feel right?** Anything sent to local that should have gone to
  cloud, or vice versa?
- **Cost / latency change** versus sending everything to the big model.
- **Any prompt where the score surprised you** — run `wayfinder route <file>
  --explain` to see which features drove it.
- **Anything that broke or felt clunky** — setup, errors, streaming.

And send a thumbs-down inline (`POST /v1/feedback`) whenever an answer wasn't good
enough — that's the data that tunes the router to your judgment.
