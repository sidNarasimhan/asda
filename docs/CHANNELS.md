# Channel setup

Keep the application in practice mode until every channel has been tested with a controlled internal recipient and campaign records have passed the readiness checklist.

## Email

Configure SMTP in **Settings → Email** or use environment variables:

- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`

For reply monitoring, configure either IMAP (`IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`) or Microsoft Graph. Verify the inbox connection before enabling live sequences so replies can pause follow-ups.

## LinkedIn / PhantomBuster

1. Create or select the ASDA Outreach PhantomBuster agents.
2. Add the PhantomBuster API key and the LinkedIn session cookie (`li_at`) in Settings.
3. Confirm the connection, inbox, and message agents report healthy.
4. Keep conservative limits: connection invites and messages must be paced and restricted to business hours.

LinkedIn profile reviews in ASDA are read-only. Do not click Connect, Message, or Follow while auditing a profile.

## WhatsApp / Wappfly

1. Connect the WhatsApp Business session in Wappfly.
2. Add `WAPPFLY_API_TOKEN` and a random `WAPPFLY_WEBHOOK_SECRET` in Settings.
3. Set the Wappfly callback URL to:

   ```text
   https://<public-asda-origin>/webhooks/wappfly/<webhook-secret>
   ```

4. Send one internal test message and confirm receipt.
5. Keep the configured WhatsApp pacing limits and business-hour window. The application is draft-first; do not enable external sending until campaign approval.

The callback URL must be reachable from Wappfly. A free Quick Tunnel changes on restart, so update the Wappfly callback when that happens.

## Apollo

Set `APOLLO_API_KEY` in Settings. Apollo plans may allow organization lookup while restricting people search/enrichment. Use it only where the plan permits it; upload CSV exports when people search is unavailable.

## SignalHire

Set `SIGNALHIRE_API_KEY` in `.env` or the runtime configuration. SignalHire enrichment is asynchronous and credit-based. Only merge a result when the LinkedIn identity matches the original lead; do not overwrite a record merely because names are similar.

## OpenRouter / research

Set `OPENROUTER_API_KEY` and choose approved models in Settings. Research and draft generation should stay review-only until the scoped campaign list is approved.
