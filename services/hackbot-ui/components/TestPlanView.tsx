import { isPlainObject, isStringArray } from "@/lib/findings-format";

type TestCaseStatus = "passed" | "failed" | "unsuitable";

interface GeneratedTestCase {
  id: number;
  title: string;
  preconditions: string | null;
  steps: string[];
}

interface TestCaseResult {
  id: number;
  status: TestCaseStatus;
  summary: string;
  failureReason: string | null;
}

export interface TestPlan {
  feature: string;
  generatedTestCases: GeneratedTestCase[];
  results: TestCaseResult[];
  summary: string;
}

function isStatus(value: unknown): value is TestCaseStatus {
  return value === "passed" || value === "failed" || value === "unsuitable";
}

export function parseTestPlan(
  findings: Record<string, unknown>
): TestPlan | null {
  const result = findings.result;
  if (!isPlainObject(result) || !Array.isArray(result.generated_test_cases)) {
    return null;
  }

  const generatedTestCases: GeneratedTestCase[] = [];
  for (const value of result.generated_test_cases) {
    if (
      !isPlainObject(value) ||
      typeof value.id !== "number" ||
      typeof value.title !== "string" ||
      !Array.isArray(value.steps) ||
      !isStringArray(value.steps)
    ) {
      return null;
    }
    generatedTestCases.push({
      id: value.id,
      title: value.title,
      preconditions:
        typeof value.preconditions === "string" ? value.preconditions : null,
      steps: value.steps,
    });
  }

  const results: TestCaseResult[] = [];
  if (Array.isArray(result.results)) {
    for (const value of result.results) {
      if (
        isPlainObject(value) &&
        typeof value.id === "number" &&
        isStatus(value.status)
      ) {
        results.push({
          id: value.id,
          status: value.status,
          summary: typeof value.summary === "string" ? value.summary : "",
          failureReason:
            typeof value.failure_reason === "string"
              ? value.failure_reason
              : null,
        });
      }
    }
  }

  return {
    feature: typeof result.feature === "string" ? result.feature : "",
    generatedTestCases,
    results,
    summary: typeof result.summary === "string" ? result.summary : "",
  };
}

export function TestPlanView({ testPlan }: { testPlan: TestPlan }) {
  const resultsById = new Map(
    testPlan.results.map((result) => [result.id, result])
  );

  return (
    <div className="test-plan">
      {testPlan.feature && (
        <section className="test-plan-section">
          <h3 className="test-plan-label">Feature name</h3>
          <p className="test-plan-feature">{testPlan.feature}</p>
        </section>
      )}
      <h3 className="test-plan-label">Test cases</h3>
      <ol className="test-case-list">
        {testPlan.generatedTestCases.map((testCase) => {
          const result = resultsById.get(testCase.id);
          const failureReason =
            result && result.status !== "passed"
              ? result.failureReason || result.summary
              : null;
          return (
            <li className="test-case" key={testCase.id} value={testCase.id}>
              <div className="test-case-heading">
                <h3>{testCase.title}</h3>
                {result && (
                  <span className={`badge test-${result.status}`}>
                    {result.status}
                  </span>
                )}
              </div>
              {failureReason && (
                <div className={`test-case-reason test-${result?.status}`}>
                  <strong>Reason</strong>
                  <p>{failureReason}</p>
                </div>
              )}
              {testCase.preconditions && (
                <div className="test-case-preconditions">
                  <h4>Preconditions</h4>
                  <p>{testCase.preconditions}</p>
                </div>
              )}
              <h4>Test steps</h4>
              <ol className="test-step-list">
                {testCase.steps.map((step, index) => (
                  <li key={index}>{step}</li>
                ))}
              </ol>
            </li>
          );
        })}
      </ol>
      {testPlan.summary && (
        <div className="test-plan-summary">
          <h4>Summary</h4>
          <p>{testPlan.summary}</p>
        </div>
      )}
    </div>
  );
}
