# Upload GOAT

Upload GOAT is a PyQt5 desktop tool that schedules TikTok uploads via the Upload-Post API and can hand them off to a Raspberry Pi for 24/7 posting.

## Headless Pi install
On Raspberry Pi we install a slim set of requirements that excludes PyQt5:

```bash
# inside the virtualenv
pip install -r requirements_pi.txt
```

This keeps build times and disk usage minimal for the headless worker. 