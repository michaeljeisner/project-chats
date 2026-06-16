# ChatGPT Business Workflow

ChatGPT Business users usually cannot export every teammate's private chat history from one admin account. Treat the workflow as per-user.

## Recommended Flow

1. The project owner creates a shared ChatGPT Project.
2. The project owner shares the Project with everyone who needs to move chats.
3. Each user runs Project Chats locally against files they are authorized to process.
4. Each user reviews `review_queue.html`.
5. Each user opens `move_queue.html` and moves approved chats into the shared Project.
6. The project owner collects handoff zips if a consolidated memory pack is needed.

## Capturing Chats

Use one of these sources:

- `conversations.json` when a user has a ChatGPT export available.
- Normalized JSON from another approved internal collector.
- Markdown/text notes copied from chats or shared chat pages.

Do not use private ChatGPT endpoints, cookies, session stores, or password stores.
