# tasks

- [x] Build a terminal monitor that probes Codex and Claude usage locally.
- [x] Refresh the dashboard every minute by default.
- [x] Add parser coverage for representative Codex and Claude output.
- [x] Document setup and usage.
- [x] Prepare the repo for GitHub with screenshots, docs, and ignore rules.
- [x] Make Gemini auth-related probe failures explicit instead of generic stats parse errors.
- [x] Repair all provider probes after CLI output changes (Codex PTY noise, Claude compact usage panel, Gemini bundled internal quota probe).
- [x] Add GitHub Copilot as a fourth monitored provider with premium request parsing and dashboard rows aligned with the other cards.
- [x] Refine Copilot card semantics to monthly remaining/reset/pace with one-decimal percentage output.
- [x] Show an explicit updating state during timed refresh instead of a frozen countdown at 0s.
- [x] Fix updating-state redraw spam and keep refresh in-place without flooding full frames.
- [x] Switch Copilot month reset target to UTC and add a color progress bar for `month rem`.
- [x] Restore live startup and countdown timers while preserving in-place terminal repaint behavior.
- [x] Replace terminal repaint strategy with cursor-relative redraw to keep animated frames in-place across terminals with inconsistent absolute-home/alternate-screen support.
