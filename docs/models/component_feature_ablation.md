**Test if any features in the component model can be removed without
impacting performance #3677**

To test if any features in the component model can be removed without
impacting the performance, feature ablation study was performed. This
process involves systematically removal of one feature at a time,
re-training the model, and then evaluation of the performance without
that feature.

This document provides a detailed comparison of various model runs based
on key evaluation metrics including Precision, Recall, F1 Score,
Specificity, Geometric Mean (Geo Mean), Index of Balanced Accuracy
(IBA), and the Support. The objective is to identify the model run with
superior performance based on these metrics.

**Evaluation Metrics**

Here is a brief explanation of the evaluation metrics used:

1. **Precision:** Measures the accuracy of the positive predictions.

2. **Recall:** Measures the fraction of the total amount of relevant
   instances that were actually retrieved.

3. **F1 Score:** The harmonic mean of Precision and Recall, indicating
   a balance between them.

4. **Specificity**: Measures the true negative rate.

5. **Geometric Mean (Geo Mean):** Another measure for the balance of
   the model regarding the classes.

6. **Index of Balanced Accuracy (IBA):** Reflects the model's balance
   across classes.

7. **Support:** Indicates the number of samples in each run.

**Model Run Comparison**

1. Precision

- Highest: Removal of feature _is_coverity_issue_ run with a precision of 0.6364.

- Lowest: Removal of featuere _severity_ run with a precision of 0.6133.

2. Recall

- Highest: Removal of feature _is_coverity_issue_ run with a recall of 0.6199.

- Lowest: Removal of feature _severity_ run with a recall of 0.6061.

3. F1 Score

- Highest: Removal of feature _is_coverity_issue_ run with an F1 score of 0.6146.

- Lowest: Removal of feature _severity_ run with an F1 score of 0.5990.

4. Specificity

- Highest: Removal of featuer _has_w3c_url_ run with a specificity of 0.9924.

- Lowest: Removal of feature _is_coverity_issue_ run with a specificity of 0.9917.

5. Geometric Mean

- Highest: Removal of feature _is_coverity_issue_ run with a geometric mean of 0.7724.

- Lowest: Removal of feature _severity_ run with a geometric mean of 0.7613.

6. Index of Balanced Accuracy (IBA)

- Highest: Removal of feature _is_coverity_issue_ run with an IBA of 0.5944.

- Lowest: Removal of feature _severity_ run with an IBA of 0.5808.

7. Support

- The support values are fairly consistent across all runs, ranging from
  7285 to 7295.

Here's the summarization of the average outcome for each run of the model after removing the
features mentioned in the first column in the following table.

> **Table 1: Summary of evaluation metrics**

| **Run specifics**   | **Precision** | **Recall** | **Specificity** | **F1 Score** | **Geo_mean** | **IBA**   | **Support** |
| ------------------- | ------------- | ---------- | --------------- | ------------ | ------------ | --------- | ----------- |
| Baseline            | 0.62371       | 0.610981   | 0.99236         | 0.603454     | 0.764278     | 0.585823  | 7285        |
| Has_crash_signature | 0.62080       | 0.608969   | 0.99204         | 0.604373     | 0.765565     | 0.583352  | 7291        |
| has_Github_URL      | 0.62499       | 0.613115   | 0.99209         | 0.607870     | 0.769815     | 0.587308  | 7289        |
| has_str             | 0.62491       | 0.611103   | 0.99181         | 0.606182     | 0.766683     | 0.585418  | 7295        |
| has_url             | 0.62406       | 0.615279   | 0.99244         | 0.608298     | 0.769200     | 0.590218  | 7291        |
| has_w3c_url         | 0.62575       | 0.615036   | 0.99244         | 0.608679     | 0.768450     | 0.589993  | 7289        |
| is_coverity_issue   | 0.63642       | 0.619942   | 0.99169         | 0.614619     | 0.772387     | 0.594384  | 7291        |
| keywords            | 0.62575       | 0.611575   | 0.99211         | 0.605720     | 0.766200     | 0.586202  | 7291        |
| landings            | 0.62053       | 0.610181   | 0.99210         | 0.604188     | 0.765553     | 0.5847866 | 7288        |
| patches             | 0.62521       | 0.610318   | 0.99219         | 0.605677     | 0.766260     | 0.5848907 | 7288        |
| severity            | 0.61327       | 0.606060   | 0.99212         | 0.599024     | 0.761271     | 0.5808348 | 7293        |
| whiteboard          | 0.62185       | 0.612788   | 0.99215         | 0.607541     | 0.767615     | 0.5872450 | 7288        |

**Conclusion**

Based on the evaluation metrics, removal of _is_coverity_issue_ component exhibits
superior performance in terms of Precision, Recall, F1 Score, Geometric
Mean, and IBA. Although it has a slightly lower specificity compared to
other runs, its higher values in other key metrics signify a better
balance and predictive accuracy. On the other hand, removal of _severity_ component
registers the lowest performance across most metrics.
