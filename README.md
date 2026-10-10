# Eurostar Snap alert

Checks Eurostar Snap every 15 minutes and emails you when a ticket appears for:

- London to Lille and Lille to London
- every Friday and Sunday in the booking window (tomorrow to 15 days ahead)
- the afternoon slot only (slots starting at 13:00 or later; from Lille the afternoon slot starts at 14:00)
- 1 adult, any price

You get one email when a slot becomes available. No repeat emails while it stays available. If it sells out and comes back, you get a new email.

If the checker cannot read the Snap website for 2 hours in a row (for example if Eurostar blocks it), you get one warning email.

It runs for free on GitHub Actions, so your laptop can be off.

---

## Setup (about 10 minutes)

### 1. Create a Gmail app password

The script sends email through your Gmail account. Google needs an "app password" for this, not your normal password.

1. Turn on 2-Step Verification on your Google account if it is not on yet: https://myaccount.google.com/security
2. Go to https://myaccount.google.com/apppasswords
3. Create an app password named "Snap alert". Copy the 16-character code.

### 2. Create a GitHub repository

1. Sign in to GitHub (create a free account if needed).
2. Create a new repository, for example `snap-alert`. Choose **Public**.

Why public: public repositories get unlimited free Actions minutes. A private repository on the free plan has a monthly minute limit, and checking every 15 minutes uses about 2,900 minutes per month, which is above it. Your email and password stay private either way, because they are stored as secrets (step 3), never in the files.

3. Upload all the files from this folder, keeping the folder structure. The workflow file must end up at `.github/workflows/snap-alert.yml`.
   - On a Mac, the `.github` folder is hidden in Finder. Press `Cmd + Shift + .` to show it, then drag everything into GitHub's "Add file > Upload files" page.
   - Or create it by hand: "Add file > Create new file", type `.github/workflows/snap-alert.yml` as the name, and paste the file content.

### 3. Add your secrets

In the repository: **Settings > Secrets and variables > Actions > New repository secret**. Add:

| Name | Value |
|---|---|
| `GMAIL_ADDRESS` | your Gmail address (the sender) |
| `GMAIL_APP_PASSWORD` | the 16-character app password from step 1 |
| `ALERT_TO` | optional: where alerts go. Leave out to send to `GMAIL_ADDRESS`. Several addresses can be separated by commas. |

### 4. Test it

1. Open the **Actions** tab. If GitHub asks, click to enable workflows.
2. Click **Eurostar Snap alert** on the left, then **Run workflow**, tick **Only send a test email**, and run. You should get a test email within a minute.
3. Run it again without the tick. Open the run and read the "Check Snap availability" step. You should see one line per trip and date, for example:

```
London to Lille  Fri 16 Oct: none
Lille to London  Sun 18 Oct: 08:35-14:00 n/a, 14:00-21:35 £55
```

After that it runs on its own every 15 minutes.

---

## Changing what it watches

Edit `config.json` in GitHub (click the file, then the pencil icon):

- `weekdays`: for example `["Friday", "Saturday", "Sunday"]`
- `earliest_slot_start`: `"13:00"` keeps afternoon slots only. Use `"00:00"` for any slot.
- `days_ahead`: how far ahead to look. Snap sells up to about 14 days before travel.
- `routes`: station codes. London St Pancras is `7015400`, Lille Europe is `8722326`. For other stations, run a search on snap.eurostar.com and copy the `origin` and `destination` numbers from the page address.

## To pause or stop

Actions tab > Eurostar Snap alert > the "..." menu > **Disable workflow**. Enable it again the same way.

## Good to know

- GitHub sometimes starts scheduled runs a few minutes late when its servers are busy.
- Snap tickets are non-refundable and sell out fast. The email has a direct booking link for each slot.
- If you get the "cannot read the Snap website" email: open the latest run in the Actions tab and download the `debug-pages` file at the bottom of the run page. It contains what the site returned, which shows whether it was a block or a page change.
- If saving state fails with a permission error: Settings > Actions > General > Workflow permissions > select "Read and write permissions".
- Test locally (optional): `pip install -r requirements.txt` then `python snap_alert.py --dry-run` prints what it finds without sending email.
