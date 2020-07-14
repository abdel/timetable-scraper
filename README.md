# Timetable Scraper

## Requirements
- Python 3.6+
- Install dependencies from `requirements.txt`

## Database Setup
Create a new SQLite3 database using `sqlite3 ./course.db`. Use the `CREATE TABLE` queries inside `db.txt` to setup the tables.

## Running the Script

`python unsw_scraper.py --year=YEAR` where `YEAR` is an integer, e.g. `python unsw_scraper.py --year=2020` to collect and store timetable data for the academic year 2020.
