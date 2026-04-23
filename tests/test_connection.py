from app.core.database import run_query


def test_neo4j_connection():
    result = run_query("RETURN 1 AS value")
    assert result[0]["value"] == 1


def test_node_count():
    result = run_query("MATCH (n) RETURN count(n) AS total")
    total = result[0]["total"]
    assert total > 0, "Database appears empty"
    print(f"Total nodes: {total}")
