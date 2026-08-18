# mechanical-7: precise configuration edit

Apply one low-risk configuration update in `requirements.lock.json`:

- Change only packages.httpx from 0.27.0 to 0.27.2.
- Preserve every unrelated key, value, array element, and JSON value type.
- Keep `TASK.md` unchanged and do not create or remove files.

This is a deliberately narrow maintenance task. Inspect the current file, make the requested edit, and verify the resulting JSON is valid before returning.
