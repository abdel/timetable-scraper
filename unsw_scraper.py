"""Scraps UNSW Class details."""

import re
import os
import time
import json

from datetime import datetime
from urllib.parse import urlparse

import sqlite3
import requests

from absl import app
from absl import flags

from tqdm import tqdm

from bs4 import BeautifulSoup

FLAGS = flags.FLAGS

flags.DEFINE_string('url', 'http://timetable.unsw.edu.au/{0}/subjectSearch.html', 'URL to scrap.')
flags.DEFINE_integer('year', 2020, 'The timetable year to scrap.')

def parse_term(tp_table_raw):
  """Parse term HTML table into a dictionary with headings."""
  tp_table = tp_table_raw.find_all('tr')[0]
  tp_table_data = tp_table.find_all('tr')

  headings = []
  for td in tp_table_data[4].find_all('td'):
    headings.append(td.text.replace('\n', ' ').strip())

  data = []

  for _, tr in enumerate(tp_table_data[6:]):
    row = {}
    for td, heading in zip(tr.find_all('td'), headings):
      if td.text == '' or td.text == heading:
        continue
      if heading == 'Enrols/Capacity':
        heading_text = heading.split('/')
        row_text = td.text.split('/')

        row[heading_text[0]] = int(re.sub('[^0-9]', '', row_text[0]) or '0')
        row[heading_text[1]] = int(re.sub('[^0-9]', '', row_text[1]) or '0')
      else:
        row[heading] = td.text

    if row and len(row) >= 6:
      data.append(row)

  return data

def get_links(soup, prefix_url):
  subject_links = []
  for subj_link in soup.find_all('a'):
    if 'href' not in subj_link.attrs or '.html' not in subj_link.attrs['href'] or 'Search' in subj_link.attrs['href']:
      continue

    subj_link_full = os.path.join(prefix_url, subj_link.attrs['href'])
    subject_links.append(subj_link_full)

  return subject_links

def parse_html(url):
  # Make a GET request to fetch the raw HTML content
  html_content = requests.get(url).text

  # Parse the html content
  soup = BeautifulSoup(html_content, 'lxml')

  return soup

def get_course(course_link, subject_code=None, year=None, cur=None):
  course_soup = parse_html(course_link)

  filename = os.path.basename(course_link)
  course_code = filename[:-5]

  form_body = course_soup.find_all('td', attrs={'class': 'formBody'})
  tables = form_body[1].find_all('table')

  # Terms Offered
  # ----------------------------------------------------------------------------
  terms_list = tables[0].find_all('table')[2]
  allowed_terms = [
      'SUMMER TERM', 'TERM ONE', 'TERM TWO', 'TERM THREE',
      'SEMESTER ONE', 'SEMESTER TWO', 'SEMESTER THREE'
  ]

  num_terms = 0
  for tr in terms_list.find_all('tr'):
    for td in tr.find_all('td'):
      if any(term_match in td.text for term_match in allowed_terms):
        num_terms += 1


  # Faculty Info
  # ----------------------------------------------------------------------------
  label = None
  value = None

  faculty_info = {}
  allowed_labels = ['Faculty', 'School', 'Campus', 'Career']

  for tr in tables[1].find_all('tr'):
    for td in tr.find_all('td'):
      if td.attrs['class'][0] == 'label':
        label = td.text.strip()
        continue
      if label and td.attrs['class'][0] == 'data':
        value = td.text.strip()
        if label in allowed_labels:
          faculty_info[label] = value
        label = None
        value = None

  if cur:
    cur.execute(
        """
        INSERT OR REPLACE INTO course (course_code, year, subject_code, faculty, campus, school, career, num_terms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(course_code, year) DO UPDATE SET
          faculty=excluded.faculty,
          school=excluded.school,
          career=excluded.career,
          num_terms=excluded.num_terms
        """,
        (
            course_code, year, subject_code, faculty_info['Faculty'], faculty_info['Campus'], faculty_info['School'],
            faculty_info['Career'], num_terms
        )
    )
  else:
    print('Terms Offered =', num_terms, '\n')
    print('Faculty Info =', json.dumps(faculty_info, indent=4), '\n')

  # Term Info
  # ----------------------------------------------------------------------------
  try:
    term_summary_tables = []

    for i, table in enumerate(tables):
      matches = table.find(
          lambda tag: tag.name == "td" and "SUMMARY OF " in tag.text)

      if matches:
        matched_table = tables[i+1]

        term_summary_tables.append(matched_table)

    for i in range(num_terms):
      term_data = parse_term(term_summary_tables[i])

      for term in term_data:
        date_time = ''

        if 'Day/Start Time' in term:
          date_time = term['Day/Start Time']

        if cur:
          cur.execute(
              """
              INSERT OR REPLACE INTO classes (
                  course_code, year, activity, period, class_code, status, enrols, capacity, date_starttime
                )
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
              ON CONFLICT(course_code, year, class_code) DO UPDATE SET
                activity=excluded.activity,
                period=excluded.period,
                status=excluded.status,
                enrols=excluded.enrols,
                capacity=excluded.capacity,
                date_starttime=excluded.date_starttime
              """,
              (
                  course_code, year, term['Activity'], term['Period'], term['Class'], term['Status'], term['Enrols'],
                  term['Capacity'], date_time
              )
          )
  except Exception as e:  # pylint: disable=broad-except
    print('Skipping term parsing for course = ' + course_link)
    print(e)


def get_subject(subject_link, prefix_url, cur=None):
  filename = os.path.basename(subject_link)
  subject_code = filename[0:4]
  campus_code = filename[4:8]

  subject_soup = parse_html(subject_link)
  course_links = get_links(subject_soup, prefix_url)

  if cur:
    cur.execute("""
      INSERT OR REPLACE INTO subject (subject_code, campus_code) VALUES (?, ?)
      ON CONFLICT(subject_code) DO UPDATE SET
        subject_code=excluded.subject_code,
        campus_code=excluded.campus_code
      """,
                (subject_code, campus_code))

  return subject_code, course_links


def main(_):
  min_year = 2008
  max_year = datetime.today().year
  year = FLAGS.year

  assert min_year <= year <= max_year, (
      'The year provided ({0}) is not within the valid period = [{1}, {2}].'.format(
          str(year), str(min_year), str(max_year)
      )
  )

  url = FLAGS.url.format(year)

  parsed_url = urlparse(url)
  prefix_url = parsed_url.scheme + '://' + os.path.join(parsed_url.netloc, str(year))

  timetable_soup = parse_html(url)
  subject_links = get_links(timetable_soup, prefix_url)

  db_path = './course.db'

  conn = sqlite3.connect(db_path)
  cur = conn.cursor()

  for subject_link in tqdm(subject_links):
    subject_code, course_links = get_subject(subject_link, prefix_url, cur)
    conn.commit()

    for course_link in tqdm(course_links):
      time.sleep(1)

      get_course(course_link, subject_code, year, cur)

      conn.commit()

  conn.close()

if __name__ == '__main__':
  app.run(main)
