# mechanical-9: precise configuration edit

Apply one low-risk configuration update in `Directory.Build.props.json`:

- Change only PropertyGroup.TreatWarningsAsErrors from the string `false` to `true`.
- Preserve every unrelated key, value, array element, and JSON value type.
- Keep `TASK.md` unchanged and do not create or remove files.

This is a deliberately narrow maintenance task. Inspect the current file, make the requested edit, and verify the resulting JSON is valid before returning.
