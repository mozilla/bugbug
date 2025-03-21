import csv
import subprocess

INPUT_CSV = "input.csv"  # Change this to your actual CSV filename
OUTPUT_CSV = "output.csv"  # The new CSV with the added column


def run_script(revision_id, diff_id, comment_id):
    """Runs the command and captures the output."""
    command = f"python3 -m scripts.comment_resolver_runner_v2 --revision-id {revision_id} --diff-id {diff_id} --comment-id {comment_id} --hunk-size 50"

    try:
        result = subprocess.run(command, shell=True, text=True, capture_output=True)
        return result.stdout.strip()  # Capture only the output
    except Exception as e:
        return f"Error: {e}"  # Return error message if something goes wrong


def process_csv(input_csv, output_csv):
    """Reads the input CSV, runs the script for each row, and writes a new CSV with the added column."""
    with open(input_csv, newline="") as infile:
        reader = csv.DictReader(infile)
        fieldnames = reader.fieldnames + ["generated-fix"]  # Add new column
        rows = []

        for row in reader:
            # Extract values
            revision_id = row["revision-id"]
            diff_id = row["diff-id"]
            comment_id = row["comment-id"]

            generated_fix = run_script(revision_id, diff_id, comment_id)

            print(
                f"generated_fix for {revision_id}, {diff_id}, {comment_id}: {generated_fix}"
            )
            row["generated-fix"] = generated_fix

            rows.append(row)

    # Write to new CSV file
    with open(output_csv, mode="w", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Processing complete. Output saved to {output_csv}")


# Run the script
process_csv(INPUT_CSV, OUTPUT_CSV)
