# Build Log and Troubleshooting

How this project was built, step by step, including the problems I hit and how I fixed them. Useful as a setup guide and as a record of what I learned.

## 1. Google Cloud project setup

- Created a Google Cloud project with billing enabled and opened Cloud Shell and the Cloud Shell Editor.
- Set the active project:
  ```bash
  gcloud projects list
  gcloud config set project YOUR_PROJECT_ID
  ```

**Issue:** `project ID unset` in a fresh Cloud Shell.
**Fix:** use the **project ID** from `gcloud projects list`, not the display name.

## 2. Python environment and ADK

```bash
python -m venv .venv
source .venv/bin/activate
pip install google-adk google-cloud-storage google-cloud-vision
```

Note: after a Cloud Shell restart, run `source .venv/bin/activate` again or packages won't be found.

## 3. Gemini API key

- Created an API key in Google AI Studio and linked it to the Cloud project.
- `adk create` stores it in the agent's `.env` file, which is git-ignored.

## 4. First agent

```bash
adk create healthy_hints   # chose gemini-3.5-flash, Google AI backend
adk run healthy_hints
```

**Issue:** `503 UNAVAILABLE: model experiencing high demand`.
**Fix:** a server-side spike; retrying worked. Later handled automatically with retry options (see step 6).

## 5. Multi-tool agent (practice tools)

- Added two simple function tools (`get_weather`, `get_current_time`) to see Gemini choose tools on its own based on function names and docstrings.
- Ran the web UI to inspect tool calls in the events panel.

**Issue:** `adk web` loaded a blank page; the server log showed `403 Forbidden` for every `.js` file.
**Fix:** Cloud Shell Web Preview uses a different origin, so allow it:
```bash
adk web --allow_origins="*"
```

**Issue:** `127.0.0.1 refused to connect` in the laptop browser.
**Fix:** the server runs inside Cloud Shell, not the laptop. Open it through **Web Preview → Preview on port 8000**.

## 6. Handling rate limits

**Issue:** `429 RESOURCE_EXHAUSTED` (free-tier quota).
**Fix:** wrapped the model with retry and exponential backoff:
```python
from google.adk.models import Gemini
from google.genai import types

model=Gemini(
    model="gemini-3.5-flash",
    retry_options=types.HttpRetryOptions(initial_delay=2, attempts=5),
)
```
Retries help with per-minute limits and 503s, not with daily quota limits.

**Issue:** `NameError: name 'Agent' is not defined` after editing imports.
**Fix:** restored `from google.adk.agents import Agent`. Running `python agent.py` before `adk web` catches import errors quickly.

## 7. Cloud Storage and Vision setup

```bash
gcloud services enable storage.googleapis.com vision.googleapis.com
gcloud storage buckets create gs://YOUR_BUCKET_NAME --location=us-west1
```

- Uploaded two label photos to `healthy_hints/images/`.
- Added settings to `healthy_hints/.env` (each on its own line) and to `~/.bashrc` for scripts run outside ADK:
  ```
  HEALTHY_HINTS_BUCKET=YOUR_BUCKET_NAME
  HEALTHY_HINTS_UPLOAD_DIR=/absolute/path/to/healthy_hints/images
  ```

## 8. Fixing the tool pipeline

Replaced the codelab's disconnected tools with linked ones:

- `upload_image(image_path)` uploads the image under a UUID name and returns its `gs://` URI.
- `read_ingredients(gcs_uri)` runs Vision OCR on exactly that URI.
- The agent instruction tells Gemini to pass the returned URI unchanged and to use only the OCR text.

**Issue:** `KeyError: 'HEALTHY_HINTS_BUCKET'`.
**Fix:** the editor's Run button opened a new terminal without the exported variables. Added the exports to `~/.bashrc`.

## 9. Direct pipeline test (no LLM)

```bash
python scripts/test_pipeline.py label_a.jpeg label_b.jpeg
```

Uploads worked immediately and returned two different UUID URIs.

**Issue:** Vision returned `403: requires a quota project` when using local Application Default Credentials.
**Fix:**
```bash
gcloud config set project YOUR_PROJECT_ID
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

Testing the tools directly separated cloud setup problems from agent problems and used no model quota.

## 10. Full agent runs and traces

```bash
adk web --allow_origins="*"
```

Prompt (text only):
> Analyze the ingredients in label_a.jpeg. Upload that file first, then use OCR on the returned GCS URI. Tell me what text was recognized and explain the ingredients with appropriate caveats.

**Issue:** `401 UNAUTHENTICATED: API key not valid`.
**Fix:** appending settings with `>>` had glued them onto the API key line in `.env`. Put each setting on its own line.

**Issue:** an early run had the photo attached in the chat, so the multimodal model could see the image directly and bypass the tools.
**Fix:** reran with text-only prompts so answers could only come through the tool pipeline.

Verified in the trace for each run: the URI returned by `upload_image` matched the URI passed to `read_ingredients`, and the two labels got different URIs and different OCR text.

## Key lessons

- **LLM vs. agent:** the model plans; tools do the work.
- **Tools must share data:** one tool's output is the next tool's input, or the pipeline isn't real.
- **Two credentials, two jobs:** the API key is for Gemini; Application Default Credentials are for Storage and Vision.
- **Read error codes:** 401 = unknown identity, 403 = not allowed, 429 = too many requests, 503 = server busy.
- **Test one layer at a time:** prove the cloud tools work before involving the LLM.
- **Verify with traces, not just answers:** matching URIs are the proof the pipeline works.