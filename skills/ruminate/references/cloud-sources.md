# Pure-Cloud Sources (Gemini Web, ChatGPT Web, Claude Web)

Consumer web chat apps store conversation history in the cloud and do not expose an official local history API. Use this 3-tier fallback in order:

## Tier 1: Official Export Archives (Recommended, 100% Portable)

Pass any downloaded `.zip`, `.json`, or `.html` export to `history_tool.py` via `--path`:

### Gemini Web (Google Takeout)
> **Common trap**: Do NOT select the top-level **Gemini** product in Google Takeout — that only exports your Gems/configurations, not your chat history.

1. Open `https://takeout.google.com` and click **Deselect all**.
2. Scroll to **My Activity** and check it.
3. Click **All activity data included** -> **Deselect all** -> check **only `Gemini Apps`** -> **OK**.
4. Change the output format from `HTML` to **`JSON`**.
5. Download the archive and pass the `.zip` or `Takeout/My Activity/Gemini Apps/MyActivity.json` to `--path`.
   - `ruminate` automatically groups flat activity events by conversation ID (`/app/c/<id>`) and preserves direct conversation links.
   - If the user accidentally exported `MyActivity.html`, `ruminate` can still extract basic prompts, but recommend JSON next time.

### ChatGPT Web
1. Open ChatGPT **Settings -> Data Controls -> Export Data**.
2. Download the `.zip` (or extract `conversations.json`) and pass it to `--path`.

### Claude Web
1. Open Claude **Settings -> Privacy -> Export Data**.
2. Download the `.zip` (or extract `conversations.json`) and pass it to `--path`.

---

## Tier 2: Read-Only Browser Capture (Only When Host Has a Browser Tool)

Use only if:
1. The host agent has a browser tool that shares the user's logged-in browser profile (never launch headless browsers or steal cookies), AND
2. The user explicitly consents to reading their web history.

**Protocol**:
- **Gemini Web**: Navigate directly to `https://myactivity.google.com/product/gemini`. This chronological feed shows prompts, timestamps, and conversation links in one place, which is faster and more reliable than opening sidebar threads one by one.
- **ChatGPT / Claude Web**: Open the sidebar history for the requested date window and read titles + relevant threads in read-only mode.
- **Human handoff**: If a login screen, 2FA prompt, or CAPTCHA / anti-bot check appears, **stop immediately** and ask the user to complete it in the browser before resuming.

---

## Tier 3: 10-Second Cloud Bridge Prompt (Zero-Tool Fallback)

When neither an export archive nor a logged-in browser tool is available, give the user this prompt to paste into Gemini / ChatGPT / Claude Web, then merge their pasted response into the final `ruminate` digest:

### English Prompt
> Review our conversations from the past 7 days (excluding simple translations, grammar checks, or one-off debugging). List:
> 1. Forgotten or half-explored ideas and "what if" sparks
> 2. Key discussions and conclusions
> 3. Unfinished threads worth revisiting
> For each item, include the approximate date and conversation title.

### 中文提示词
> 请回顾我们过去 7 天的对话（过滤掉简单翻译、语法润色和单次修报错），帮我整理：
> 1. 容易遗忘的新奇点子或未深入的灵感火花
> 2. 有价值的深度讨论与关键结论
> 3. 聊到一半搁置、值得继续的线索
> 每条请附带大致日期和对话标题。
