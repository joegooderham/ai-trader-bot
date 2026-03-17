# Backlog

Tracked items for future development. Referenced in commit messages as `BACKLOG-XXX`.

---

## BACKLOG-046: Cloudflare dashboard landing page with branding
**Priority:** Medium | **Area:** Infrastructure / UI

Customise the Cloudflare Access login/splash page for the dashboard:
- Add company/project logo
- Add legal disclaimer / terms of use jargon
- Style to match the dashboard's dark theme
- Configured at the Cloudflare Access application level (custom block page or Access app appearance settings)

---

## BACKLOG-047: Cloudflare tunnel drops when laptop lid is closed
**Priority:** High | **Area:** Infrastructure / Reliability

The Cloudflare tunnel running on the self-hosted Windows runner goes down when the laptop lid is closed (sleep/hibernate). This breaks dashboard access and CI/CD webhook delivery until the lid is reopened.

Possible solutions:
- **Power settings**: Disable sleep on lid close in Windows power options (`powercfg /setacidctimeout 0`, or Control Panel > Power Options > "Do nothing" on lid close)
- **Cloudflare Tunnel as a Windows service**: Run `cloudflared` as a Windows service instead of a user-space process — services survive sleep/wake better
- **Keep-alive script**: Watchdog that detects tunnel drop and restarts `cloudflared` on wake
- **Move to always-on infrastructure**: Migrate the runner to a VM or cloud instance that doesn't sleep (Oracle Cloud free tier, etc.)

---
