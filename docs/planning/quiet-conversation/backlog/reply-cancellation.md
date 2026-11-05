# Cancelling a reply in progress

State: backlog; not started. Left out of the voice and audio core build at Tim's decision (2026-11-04).

The delivery plan describes three distinct actions: Skip the rest, Stop speaking and Cancel request. The core build delivers Skip and Stop, which only silence audio and cancel nothing. There is no way today to cancel a brain turn that is still being generated; runs and message cancel windows keep their own cancellation. Adding reply cancellation changes how the brain's turns run, so it needs its own design: what is cancelled (generation, pending tool calls), what an already executed action means, and the page and brain-callable controls in English and Spanish.
