# mechanical-8: precise configuration edit

Apply one low-risk configuration update in `.github/dependabot.json`:

- Change only updates[0].schedule.interval from monthly to weekly.
- Preserve every unrelated key, value, array element, and JSON value type.
- Keep `TASK.md` unchanged and do not create or remove files.

This is a deliberately narrow maintenance task. Inspect the current file, make the requested edit, and verify the resulting JSON is valid before returning.
