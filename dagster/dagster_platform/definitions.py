# dagster_platform/definitions.py
# Minimal Dagster code location. Exports an empty Definitions object so the
# dagster-webserver can load the code location without error.
# Assets, jobs, schedules, and sensors are added in later sprints (F-005+).

from dagster import Definitions

defs = Definitions()
