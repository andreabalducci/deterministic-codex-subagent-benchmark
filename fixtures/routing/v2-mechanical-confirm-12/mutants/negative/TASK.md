# mechanical-12: precise configuration edit

Apply one low-risk configuration update in `compose.images.json`:

- Change only services.cache.image from redis:7.2-alpine to redis:7.4-alpine.
- Preserve every unrelated key, value, array element, and JSON value type.
- Keep `TASK.md` unchanged and do not create or remove files.

This is a deliberately narrow maintenance task. Inspect the current file, make the requested edit, and verify the resulting JSON is valid before returning.
