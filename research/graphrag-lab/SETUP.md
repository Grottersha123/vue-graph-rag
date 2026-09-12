# Setup

Окружение уже создано и проверено (2026-09-03):

```bash
cd "📚 Areas/Evil Unicorn/graphrag-lab"
uv venv .venv --python 3.13
VIRTUAL_ENV="$PWD/.venv" uv pip install ladybug
```

Проверка перед каждой сессией:

```bash
.venv/bin/python -c "import ladybug; print(ladybug.__version__)"
# ожидание: 0.20.2
```

Мини-smoke:

```python
import ladybug as lbug
db = lbug.Database(database_path=":memory:")
conn = lbug.Connection(db)
conn.execute("CREATE NODE TABLE Person(name STRING, PRIMARY KEY(name))")
conn.execute("CREATE (:Person {name: 'Аня'})")
print(conn.execute("MATCH (p:Person) RETURN p.name").get_next())
# ожидание: ['Аня']
```

Документация: https://docs.ladybugdb.com/
