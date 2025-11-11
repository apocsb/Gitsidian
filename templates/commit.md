# ---
# YAML frontmatter: use YAML-escaped placeholders (generated as JSON strings) to ensure validity
title: {{title_yaml}}
sha: {{sha_yaml}}
short: {{short_yaml}}
author: {{author_yaml}}
email: {{email_yaml}}
date: {{date_yaml}}
branch: {{branch_yaml}}
parents: {{parents_json}}
tags: ["git","commit","{{repo}}","{{branch}}"]
# ---

# {{title}}

## Parents
{{parents_list}}

## Message
{{body}}

## Diff stats
```
{{diffstat}}
```

{{#if diff}}
## Diff
```
{{diff}}
```
{{/diff}}
