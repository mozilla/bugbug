import csv
from get_bugs import get_bugs
import bugbug


with open('classes.csv', 'r') as f:
    classes = [row for row in csv.reader(f)][1:]

bugs = get_bugs([int(bug_id) for bug_id, is_bug in classes])

true_positives = 0
true_negatives = 0
false_positives = 0
false_negatives = 0

for bug_id, is_bug in classes:
    assert is_bug == 'True' or is_bug == 'False'
    bug_id = int(bug_id)
    is_bug = True if is_bug == 'True' else False

    print(bug_id)
    is_bug_pred = bugbug.is_bug(bugs[bug_id])

    if is_bug_pred and is_bug:
        true_positives += 1
    elif not is_bug_pred and not is_bug:
        true_negatives += 1
    elif is_bug_pred and not is_bug:
        false_positives += 1
    elif not is_bug_pred and is_bug:
        false_negatives += 1

print('Accuracy: {:.3%}'.format(float(true_positives + true_negatives) / len(classes)))
print('Precision: {:.3%}'.format(float(true_positives) / (true_positives + false_positives)))
print('Recall: {:.3%}'.format(float(true_positives) / (true_positives + false_negatives)))
print('Specificity: {:.3%}'.format(float(true_negatives) / (true_negatives + false_positives)))
