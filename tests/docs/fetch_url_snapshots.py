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
"""

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


if __name__ == "__main__":
    fetch_climate()
    fetch_mortality()
