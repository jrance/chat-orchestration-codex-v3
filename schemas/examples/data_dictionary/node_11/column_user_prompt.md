Write an English description for EACH column in this database object.

Return JSON only.

DOMAIN GLOSSARY (may be empty):
{{form.domain_glossary}}

OBJECT IDENTITY:
- schema: {{row.schema}}
- name: {{row.name}}
- type: {{row.type}}   (table or view)

OBJECT PURPOSE / GRAIN (if already generated):
{{row.object_purpose_and_grain}}

OBJECT METADATA (catalog):
{{row.object_metadata}}

TABLE/VIEW PROFILE (may be empty):
{{row.table_profile}}

COLUMN METADATA + PROFILES
You will receive the columns list and (optionally) profiling data per column.
- columns: {{row.columns}} 
- column_profiles: {{row.column_profiles}}

INSTRUCTIONS:
1) For every column, produce:
   - description (1–2 sentences, business friendly)
   - semantic_type (choose one of the allowed enums)
   - is_identifier (true/false)
   - is_sensitive (true/false)
   - confidence 0.0–1.0
   - open_questions (0–4) only if meaning is unclear
   - evidence (1–4 short phrases referencing the strongest hints)
2) Use these cues as evidence (if present):
   - Column name patterns (e.g., *_id, *_date, status/type/code, is_*)
   - Data type and nullability
   - Primary/foreign key membership from object_metadata
   - Top values / distinct / null pct / min/max from column_profiles
3) If you mark is_sensitive=true, keep the description generic (e.g., “Customer email address”), and do NOT echo sample values.
4) If evidence conflicts (e.g., name implies date but type is int), lower confidence and add an open question.
5) Do not invent enumerations; if top values show a small set, describe it as “appears to be a small set of statuses such as …” without claiming completeness.
