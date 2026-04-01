# arXiv Secretary

A lightweight desktop GUI for tracking arXiv authors, institution keywords, and research topics.

## Features

- Save watch targets locally with SQLite
- Track three watch types:
  - `Author`: mapped to arXiv author search
  - `Topic`: mapped to full-text metadata search
  - `Institution`: mapped to keyword search against available arXiv metadata
- Fetch a `Latest` view for your tracked interests
- Build a `Daily Digest` from papers published in the last 24 hours
- Generate an `AI Summary` of the daily digest with OpenAI or Anthropic
- Configure scheduled alerts with daily, weekly, or monthly timing
- Send desktop pop-up alerts and optional email notifications through SMTP
- De-duplicate overlapping matches and show which watch targets matched each paper
- Open the paper abstract page or PDF in your browser

## Run

```powershell
python main.py
```

The app stores its data in `arxiv_secretary.db` in the project folder.

## Notes

- arXiv's public API does not provide a structured institution affiliation field. Institution tracking therefore works as a best-effort keyword search over available metadata.
- The GUI is built with the Python standard library, so no extra packages are required.
- AI provider keys are stored locally in `arxiv_secretary.db` as plain text.
- Alert emails use your own SMTP credentials and also store those credentials locally in `arxiv_secretary.db` as plain text.
- Scheduled alerts run while the app is open. Native OS push notifications are not implemented yet.
