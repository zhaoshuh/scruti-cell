---
name: code-review
description: Use when the user asks for a code review, PR review, or asks you to check code for bugs, style issues, security vulnerabilities, or best practice violations. Also use when asked to "review this file", "check this code", or "audit this implementation".
---

# Code Review

Perform thorough, constructive code reviews. Focus on correctness, maintainability, security, and adherence to the codebase's existing conventions.

## Review checklist

For each file under review, systematically check:

### 1. Correctness
- Does the code do what it claims to do?
- Are edge cases handled (null, empty, boundaries)?
- Are error conditions properly managed?
- Is there any dead code or unreachable logic?

### 2. Security
- Are there any injection risks (SQL, command, path traversal)?
- Are secrets, tokens, or credentials exposed?
- Is user input properly validated and sanitized?
- Are authentication/authorization checks in the right places?

### 3. Performance
- Are there N+1 queries or unnecessary loops?
- Are expensive operations cached or memoized where appropriate?
- Are there obvious memory leaks (unclosed connections, listeners)?
- Is there unbounded data processing?

### 4. Maintainability
- Are names clear and descriptive?
- Is the code DRY (no significant duplication)?
- Are functions and classes reasonably sized?
- Are complex sections commented or self-documenting?

### 5. Style & Conventions
- Does the code follow the project's existing patterns?
- Are imports organized and unused ones removed?
- Is formatting consistent with the codebase?
- Does it match the conventions of adjacent files?

## How to deliver the review

1. **Start with a summary**: One or two sentences on the overall quality.
2. **Categorize findings** by severity:
   - **Critical**: Must fix before merge (bugs, security, data loss)
   - **Important**: Should fix (design issues, maintainability)
   - **Nit**: Nice to fix (style, naming preferences)
3. **Be specific**: Reference exact file paths and line numbers.
4. **Suggest fixes**: When pointing out a problem, offer a concrete alternative.
5. **Acknowledge good work**: Highlight well-written sections, not just issues.
6. **End with a verdict**: Approve, approve with suggestions, or request changes.

## Rules

- Review only the code the user asks about, not unrelated files.
- If asked to review a PR, check the diff, not the full file (unless the full file context is needed).
- Do not make changes during a review unless explicitly asked to fix something.
- Be constructive, not combative. The goal is better code, not proving how much you know.
