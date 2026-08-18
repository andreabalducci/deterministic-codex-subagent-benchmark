# mechanical-2: precise configuration edit

Apply one low-risk configuration update in `tsconfig.app.json`:

- Change only compilerOptions.noUncheckedIndexedAccess from false to true.
- Preserve every unrelated key, value, array element, and JSON value type.
- Keep `TASK.md` unchanged and do not create or remove files.

This is a deliberately narrow maintenance task. Inspect the current file, make the requested edit, and verify the resulting JSON is valid before returning.
