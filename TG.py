"""Manual/local CLI runner: fetch matching eTenders and write them to xlsx."""

from pipeline import build_matches


def main():
    print("Fetching releases...")
    df = build_matches()
    if df.empty:
        print("Nothing returned. Widen LOOKBACK_DAYS.")
        return

    out = "etenders_matches.xlsx"
    df.to_excel(out, index=False)
    print(f"{len(df)} scored matches written to {out}\n")
    print(df[["score", "tender_number", "buyer", "closing", "title"]].head(25).to_string(index=False))


if __name__ == "__main__":
    main()
