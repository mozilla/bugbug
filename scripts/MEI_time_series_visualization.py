import datetime
import os
import sys
from datetime import timedelta
from typing import Dict, List

import matplotlib.pyplot as plt
import pandas as pd


def setup_bugzilla_import():
    # Path to the directory where `bugzilla.py` is located
    base_dir = os.getcwd()  # Get the current working directory

    bugbug_dir = os.path.join(base_dir)
    bugzilla_dir = os.path.join(base_dir, "bugbug")
    bugzilla_dir = os.path.abspath(bugzilla_dir)  # Convert to absolute path

    # Add this directory to the system path
    if bugbug_dir not in sys.path:
        sys.path.insert(0, bugbug_dir)
    if bugzilla_dir not in sys.path:
        sys.path.insert(0, bugzilla_dir)

    from bugzilla import calculate_maintenance_effectiveness_indicator

    return calculate_maintenance_effectiveness_indicator


# Call the function to set up the import
calculate_maintenance_effectiveness_indicator = setup_bugzilla_import()

file_path = "./bugbug/data/bug_sample.json"

# If the JSON file is line-delimited (each line is a complete JSON object),
# use the 'lines' parameter:
df = pd.read_json(file_path, lines=True)

# Define the team
team = "DOM LWS"

# Define the date range
start_year = 2023
end_year = 2023

# Create a dictionary to hold the MEI and other values for each week and delta
values: Dict[str, List] = {
    "week": [],
    "delta": [],
    "MEI": [],
    "BDTime": [],
    "WBDTime": [],
    "Incoming": [],
    "Closed": [],
}

# Loop through each year in the specified range
for year in range(start_year, end_year + 1):
    # Loop through each week in the year
    start_date = datetime.date(year, 1, 1)
    end_date = datetime.date(year + 1, 1, 1)
    week = timedelta(weeks=1)
    current_week = start_date
    while current_week < end_date:
        # Calculate deltas
        for delta in [timedelta(weeks=1), timedelta(days=90)]:
            from_date = current_week - delta
            to_date = current_week

            # Call the calculate_maintenance_effectiveness_indicator function
            result = calculate_maintenance_effectiveness_indicator(
                teams=[team], from_date=from_date, to_date=to_date
            )

            # Store the values
            values["week"].append(current_week)
            values["delta"].append(delta.days)
            values["MEI"].append(result["stats"]["ME"])
            values["BDTime"].append(result["stats"]["BDTime"])
            values["WBDTime"].append(result["stats"]["WBDTime"])
            values["Incoming"].append(result["stats"]["Incoming vs total open"])
            values["Closed"].append(result["stats"]["Closed vs total open"])

        current_week += week

# Convert the data to a DataFrame for easier plotting
df = pd.DataFrame(values)


# Plotting
plt.figure(figsize=(15, 7))
for delta in df["delta"].unique():
    subset = df[df["delta"] == delta]
    plt.plot(subset["week"], subset["MEI"], marker="o", label=f"Delta {delta} days")

# Set the y-axis limit to start from 0 and go up to 500% maximum
plt.ylim(0, 500)

# Add a horizontal 100% line emphasized more than other grid lines
plt.axhline(y=100, color="red", linestyle="--", linewidth=2, label="100%")

plt.title("Maintenance Effectiveness Indicator (MEI) Over Weeks for team: DOM LWS")
plt.xlabel("Week")
plt.ylabel("MEI Value")
plt.legend()
plt.grid(True)
plt.show()


delta_90_days_df = df[df["delta"] == 90]
delta_90_days_df.head()
# Scale the Incoming and Closed values to represent a yearly percentage
delta_90_days_df["Scaled_Incoming"] = (delta_90_days_df["Incoming"] / 52) * 100
delta_90_days_df["Scaled_Closed"] = (delta_90_days_df["Closed"] / 52) * 100


# Create a figure and the first axis (left)
fig, ax1 = plt.subplots(figsize=(15, 7))

# Plot BD Time and WBD Time on the first axis
ax1.plot(
    delta_90_days_df["week"],
    delta_90_days_df["BDTime"],
    marker="o",
    label="BD Time",
    color="b",
)
ax1.plot(
    delta_90_days_df["week"],
    delta_90_days_df["WBDTime"],
    marker="o",
    label="WBD Time",
    color="g",
)

# Set labels and limits for the first axis
ax1.set_xlabel("Week")
ax1.set_ylabel("Time (in hours)", color="b")
ax1.set_ylim(0, 15)  # Set the y-axis limit for BD Time and WBD Time
ax1.legend(loc="upper left")

# Create a second axis (right) sharing the same x-axis
ax2 = ax1.twinx()

# Plot Scaled Incoming and Scaled Closed on the second axis
ax2.plot(
    delta_90_days_df["week"],
    delta_90_days_df["Scaled_Incoming"],
    label="Scaled Incoming",
    marker="o",
    linestyle="-",
    markersize=5,
    color="r",
)
ax2.plot(
    delta_90_days_df["week"],
    delta_90_days_df["Scaled_Closed"],
    label="Scaled Closed",
    marker="s",
    linestyle="-",
    markersize=5,
    color="purple",
)

# Set labels and limits for the second axis
ax2.set_ylabel("Percentage of Defects Backlog", color="r")
ax2.set_ylim(
    0,
    max(
        delta_90_days_df["Scaled_Incoming"].max(),
        delta_90_days_df["Scaled_Closed"].max(),
    )
    + 10,
)  # Adjust the y-axis limit as needed
ax2.legend(loc="upper right")

# Add a title and grid
plt.title(
    "BD Time, WBD Time, Scaled Incoming, and Scaled Closed Over 12 Months (2023) with 3-Month Delta for team: DOM LWS"
)
plt.grid(True)

# Show the plot
plt.tight_layout()
plt.show()
