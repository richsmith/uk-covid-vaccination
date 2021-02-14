from collections import defaultdict
from datetime import datetime
from datetime import timedelta
from functools import lru_cache
import itertools

import vaccination_record


UK_POPULATION = 68_107_747
UK_ADULT_POPULATION = int(UK_POPULATION * (1 - 0.213))
PROPORTION_IMMUNE = 0.2
JAB_GAP_DAYS = 7 * 12

FIRST_VACCINATION_DAY = datetime(2020, 12, 8)


class Milestone():

    def __init__(self, description, condition):
        self.description = description
        self.condition = condition
        self.passed = False

    def check(self, population):
        self.passed = self.condition(population)
        return self.passed


class Population():

    def __init__(self, total, proportion_immune):
        self.total = total
        self.proportion_immune = proportion_immune
        self.second_dose_due = 0
        self.first_dose_given = 0
        self.second_dose_given = 0
        self.first_dose_by_day = []

    @property
    def first_dose_due(self):
        return self.total - self.first_dose_given

    @property
    def total_vaccinations(self):
        return self.first_dose_given + self.second_dose_given

    def update_day(self, day):
        self.first_dose_by_day.append(0)
        day_to_check = day - JAB_GAP_DAYS
        if day_to_check >= 0:
            self.second_dose_due += self.first_dose_by_day[day_to_check]

    def give_first_jab(self, day, n):
        self.first_dose_given += n
        self.first_dose_by_day[day] = n

    def give_second_jab(self, n):
        self.second_dose_due -= n
        self.second_dose_given += n

    def all_vaxxed(self):
        return self.second_dose_given >= self.total

    def partly_immune(self):
        naturally_immune = int((self.total - self.first_dose_given) * self.proportion_immune)
        return self.first_dose_given + naturally_immune

    def as_prop(self, n):
        return n / self.total


def run():
    milestones = [
        Milestone('60% 1st vax', lambda pop: pop.as_prop(pop.first_dose_given) >= 0.6),
        Milestone('70% 1st vax', lambda pop: pop.as_prop(pop.first_dose_given) >= 0.7),
        Milestone('80% 1st vax', lambda pop: pop.as_prop(pop.first_dose_given) >= 0.8),
        Milestone('100% 1st vax', lambda pop: pop.as_prop(pop.first_dose_given) >= 1),
        Milestone('60% 2nd vax', lambda pop: pop.as_prop(pop.second_dose_given) >= 0.6),
        Milestone('70% 2nd vax', lambda pop: pop.as_prop(pop.second_dose_given) >= 0.7),
        Milestone('80% 2nd vax', lambda pop: pop.as_prop(pop.second_dose_given) >= 0.8),
        Milestone('100% 2nd vax', lambda pop: pop.as_prop(pop.second_dose_given) >= 1),
        Milestone('Bare R <1?', lambda pop: pop.as_prop(pop.partly_immune()) >= 0.75),
    ]

    passed_milestones = []

    population = Population(UK_ADULT_POPULATION, PROPORTION_IMMUNE)

    for i, day in enumerate(days_from(FIRST_VACCINATION_DAY)):
        population.update_day(i)

        vaccines_today = get_vaccines(day)
        vaccinate(i, population, vaccines_today)

        newly_passed = get_newly_passed_milestones(population, day, milestones)
        passed_milestones.extend(newly_passed)

        if population.all_vaxxed():
            break

    for day, milestone in passed_milestones:
        iso_date = day.strftime('%Y-%m-%d')
        print(f'{iso_date}: {milestone.description}')


def vaccinate(day, population, jabs):
    second_jabs = min(population.second_dose_due, jabs)
    if second_jabs:
        jabs -= second_jabs
        population.give_second_jab(second_jabs)

    first_jabs = min(population.first_dose_due, jabs)
    if first_jabs:
        jabs -= first_jabs
        population.give_first_jab(day, first_jabs)


def get_newly_passed_milestones(population, day, milestones):
    newly_passed = []
    for milestone in milestones:
        if not milestone.passed:
            passed = milestone.check(population)
            if passed:
                if milestone is milestones[-1]:
                    # spesh case for the R<1 estimate: need 2+ weeks
                    # to get immunity from 1st jab
                    day = day + timedelta(weeks=2)
                newly_passed.append((day, milestone))
    return newly_passed


def get_vaccines(day):
    iso_date = day.strftime('%Y-%m-%d')
    dates_to_totals, last_7_day_mean = get_vaccine_data()
    return dates_to_totals.get(iso_date) or last_7_day_mean


@lru_cache
def get_vaccine_data():
    data = vaccination_record.data['data']

    recorded_days_to_totals = {}
    for record in data:
        date = record['date']
        total = (
            record.get('newPeopleVaccinatedFirstDoseByPublishDate') or
            # below is because the 1st record only includes previous
            # jabs to that day; we try to adjust for this below
            record['cumPeopleVaccinatedFirstDoseByPublishDate'])
        recorded_days_to_totals[date] = total

    days_in_order = sorted(recorded_days_to_totals.keys())

    first_recorded_day = days_in_order[0]
    total_to_first_recorded_day = recorded_days_to_totals[first_recorded_day]
    first_recorded_datetime = datetime.strptime(days_in_order[0], '%Y-%m-%d')
    days_covered = (first_recorded_datetime - FIRST_VACCINATION_DAY).days
    mean_first_period = int(total_to_first_recorded_day / days_covered)

    last_7_days = days_in_order[-7:]
    last_7_day_values = [recorded_days_to_totals[day] for day in last_7_days]
    last_7_day_mean = int(sum(last_7_day_values) / len(last_7_day_values))

    # Default to last 7 days of vax data we have for future days
    # (probably pessimistic!)
    iso_dates_to_totals = defaultdict(lambda: last_7_day_mean)
    # We don't have day-to-day data for first few weeks, so approximate
    # by averaging out the known total across this period
    for day in date_range(FIRST_VACCINATION_DAY, first_recorded_datetime, inclusive=True):
        iso_date = day.strftime('%Y-%m-%d')
        iso_dates_to_totals[iso_date] = mean_first_period
    # Include the day-by-day data we do have
    for day in days_in_order[1:]:
        iso_dates_to_totals[day] = recorded_days_to_totals[day]

    return iso_dates_to_totals, last_7_day_mean

def date_range(start, end, inclusive=False):
    range_as_int = int((end - start).days)
    for n in range(range_as_int + (1 if inclusive else 0)):
        yield start + timedelta(days=n)


def days_from(start_day):
    current_day = start_day
    for i in itertools.count(start=0):
        yield current_day
        current_day += timedelta(days=1)


if __name__ == '__main__':
    run()
