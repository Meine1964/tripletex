# Test Cases

Task prompts for testing the Tripletex agent locally.

## Auto-captured
Files named `YYYYMMDDTHHMMSS_<hash>.json` are auto-captured from live runs.

## Manual cases  
Files with descriptive names (e.g., `invoice_no_send.json`) are manually created.

## Format
```json
{
  "prompt": "The task prompt text",
  "files": [],
  "captured_at": "20260320T120000",
  "notes": "Optional description"
}
```

## Adding cases
Just drop a `.json` file here following the format above.
The `files` array can include `{"filename": "...", "mime_type": "..."}` entries.
For auto-capture, deploy the agent and run tasks — they get saved here automatically.
