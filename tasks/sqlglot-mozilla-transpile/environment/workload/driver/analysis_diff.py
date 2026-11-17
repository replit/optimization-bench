"""Protected complete-result contract, copied from the reviewed probe."""



def operation(case):
    import sqlglot
    from sqlglot.diff import Insert, Remove, Move, Update, Keep
    from analysis_edits import materialize
    dialect = case.get("dialect")

    def prepare(sql):
        statements = sqlglot.transpile(sql, read=dialect, write=dialect,
                                       pretty=True, comments=False)
        if len(statements) != 1:
            raise ValueError("Exactly one source statement is required")
        return statements[0], sqlglot.parse_one(statements[0], read=dialect)

    answer, source = prepare(case["answer"])
    comparisons = []
    weights = {Keep: 0.0, Update: 1.5, Insert: 1.0, Remove: 1.0, Move: 0.5}
    for reference in case["references"]:
        text, target = prepare(reference)
        edits = sqlglot.diff(source, target, matchings=None, delta_only=False,
                             f=0.6, t=0.6, dialect=dialect)
        counts = [len(list(source.walk())), len(list(target.walk()))]
        maximum = max(counts)
        score = 1 - sum(weights[type(edit)] for edit in edits) / maximum if maximum else 1.0
        comparisons.append({
            "normalized_reference": text, **materialize(source, target, edits),
            "node_counts": counts, "similarity_score": score,
        })
    return {"normalized_answer": answer, "comparisons": comparisons,
            "best_similarity": max((row["similarity_score"] for row in comparisons), default=-1.0)}


def run(cases):
    from analysis_common import run_cases
    return run_cases("scratch-structural-diff-v1", cases, operation)
