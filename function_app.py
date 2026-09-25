import logging

import azure.functions as func

from email_sender import send_failure_notice, send_report
from pipeline import build_matches

app = func.FunctionApp()


# 06:00 and 18:00 UTC = 08:00 and 20:00 SAST (UTC+2), twice daily.
@app.timer_trigger(schedule="0 0 6,18 * * *", arg_name="timer", run_on_startup=False)
def etenders_digest(timer: func.TimerRequest) -> None:
    if timer.past_due:
        logging.warning("Timer is past due: this run fired later than scheduled.")
    try:
        logging.info("Fetching eTenders releases...")
        df, missing = build_matches()
        logging.info("%d tenders found; %d date chunk(s) missing", len(df), len(missing))

        subject = f"eTenders digest - {len(df)} tender(s)"
        if missing:
            subject += " (partial)"
        send_report(df, subject=subject, missing=missing)
        logging.info("Email sent.")
    except Exception as exc:
        logging.exception("eTenders digest run failed")
        try:
            send_failure_notice(exc)
        except Exception:
            logging.exception("Also failed to send the failure notice email")
        raise
