#!/usr/bin/env python3
"""Refresh the real-world docs snapshots from their live public sources.

This is a manual/occasional tool, NOT part of `make docs` — the docs
build stays offline and deterministic by using the committed
climate_url_snapshot.csv / mortality_url_snapshot.csv. Re-run this
script by hand every so often to pull a fresh cut of the same public
datasets, then commit the updated CSVs.

Sources:
  - Our World in Data CO2 & greenhouse gas emissions dataset
    https://github.com/owid/co2-data
  - Our World in Data excess mortality dataset (P-scores)
    https://github.com/owid/covid-19-data (public/data/excess_mortality)
  - The GitHub REST API, for the api-sources example -- recorded as
    github_api_snapshot.json and replayed by render_github_api.py
"""

import datetime
import json
import urllib.parse

import pandas as pd


def fetch_climate():
    url = "https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv"
    df = pd.read_csv(url)
    countries = ["Norway", "Germany", "United States"]
    sub = df[df["country"].isin(countries) &
             df["year"].between(1900, df["year"].max())]
    sub = sub[["country", "year", "co2"]].dropna()
    sub.to_csv("climate_url_snapshot.csv", index=False)
    print("wrote climate_url_snapshot.csv", sub.shape)


def fetch_mortality():
    url = ("https://raw.githubusercontent.com/owid/covid-19-data/master/"
           "public/data/excess_mortality/excess_mortality.csv")
    df = pd.read_csv(url)
    countries = ["United States", "Germany", "France"]
    sub = df[df["location"].isin(countries)][
        ["location", "date", "p_scores_all_ages"]].dropna()
    #- The source "date" column is a string (e.g. "2020-01-31"), which
    #- cicwave would plot as a categorical axis (one tick per unique
    #- value). Convert to a fractional year so it plots as a normal
    #- continuous numeric axis, matching the climate snapshot's "year".
    d = pd.to_datetime(sub["date"])
    sub = sub.assign(year=d.dt.year + (d.dt.dayofyear - 1) / 365.25)
    sub[["location", "year", "p_scores_all_ages"]].round({"year": 3}).to_csv(
        "mortality_url_snapshot.csv", index=False)
    print("wrote mortality_url_snapshot.csv", sub.shape)


#- Fields github_api_spec.yaml reads, plus the ones that make a recorded
#- response recognisable as what the API returned. The full response
#- carries a `patch` per file -- a megabyte of diff text for a fixture
#- that only ever reads three numbers.
_FILE_FIELDS = ("filename", "additions", "deletions", "changes", "status")
_PULL_FIELDS = ("number", "title", "state")


def _trim(payload):
    """Keep the fields the spec uses; drop the diff text around them."""
    if not isinstance(payload, list):
        return payload
    out = []
    for rec in payload:
        if not isinstance(rec, dict):
            out.append(rec)
            continue
        fields = _FILE_FIELDS if "filename" in rec else _PULL_FIELDS
        out.append({k: rec[k] for k in fields if k in rec})
    return out


def _snapshot_key(url):
    """Path plus normalised query, so replay does not depend on ordering."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.urlencode(
        sorted(urllib.parse.parse_qsl(parts.query)))
    return parts.path + ("?" + query if query else "")


def fetch_github():
    """Record every response github_api_spec.yaml asks for.

    Driven through cicwave's own fetcher rather than a hand-written walk
    of the endpoints, so the snapshot always covers exactly the requests
    the committed spec issues -- edit the spec and re-run, and the
    recording follows.
    """
    from cicwave import apisource
    from cicwave.pivot import load_spec

    recorded = {}
    real = apisource.fetch_url_bytes

    def recording_fetch(url, **kwargs):
        data, headers = real(url, **kwargs)
        recorded[_snapshot_key(url)] = _trim(json.loads(data))
        return data, headers

    apisource.fetch_url_bytes = recording_fetch
    try:
        df = apisource.fetch_dataframe(load_spec("github_api_spec.yaml"))
    finally:
        apisource.fetch_url_bytes = real

    with open("github_api_snapshot.json", "w") as fh:
        json.dump({
            "note": ("Recorded from the live GitHub REST API by "
                     "fetch_url_snapshots.py; replayed by "
                     "render_github_api.py so `make docs` stays offline. "
                     "Per-file `patch` text is dropped."),
            "fetched": datetime.datetime.now(
                datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "responses": recorded,
        }, fh, indent=1, sort_keys=True)
    print("wrote github_api_snapshot.json",
          "(%d response(s), %d row(s))" % (len(recorded), len(df)))


if __name__ == "__main__":
    fetch_climate()
    fetch_mortality()
    fetch_github()
