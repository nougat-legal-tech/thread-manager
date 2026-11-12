# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: chat-public-host.spec.ts >> chat.nougat.law serves login and keeps OAuth URLs on the current host
- Location: ..\..\nougat\Chatbot\e2e\specs\nougat-public-host\chat-public-host.spec.ts:4:7

# Error details

```
Error: browserType.launch: Executable doesn't exist at C:\Users\aibns\AppData\Local\ms-playwright\chromium_headless_shell-1234\chrome-headless-shell-win64\chrome-headless-shell.exe
╔════════════════════════════════════════════════════════════╗
║ Looks like Playwright was just installed or updated.       ║
║ Please run the following command to download new browsers: ║
║                                                            ║
║     npx playwright install                                 ║
║                                                            ║
║ <3 Playwright Team                                         ║
╚════════════════════════════════════════════════════════════╝
```