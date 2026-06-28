---
name: planning-with-files
description: Use when planning complex multi-step tasks, managing workflows with multiple files, or tracking progress across a large codebase. Creates and maintains plan.md / plan-*.md files to organize work into phases with checklists for incremental progress tracking.
---

# Planning with Files

When working on complex tasks that span multiple files and steps, use planning files to organize your approach.

## When to use

Use this skill when:
- The user asks you to implement a feature that touches multiple files
- The task requires careful sequencing of dependent changes
- You need to track progress across a large refactoring
- The user explicitly asks for a plan or roadmap
- You start work but realize the task is more complex than initially estimated

## How to plan

1. **Create a `plan.md` file** (or `plan-<feature>.md` for feature-specific plans) in the project root or `.opencode/` directory.

2. **Structure the plan** with:
   - A brief summary of the goal
   - Phased breakdown (Phase 1, Phase 2, etc.)
   - Each phase contains a checklist of concrete, verifiable steps
   - Use `- [ ]` for pending items and `- [x]` for completed items

3. **Keep the plan updated** as you work:
   - Mark items complete as you finish them
   - Add new items as you discover them
   - Strike through cancelled items rather than deleting them

4. **Reference the plan** when reporting progress to the user.

## Example plan format

```markdown
# Plan: <Feature Name>

## Summary
Brief description of what we're building.

## Phase 1: Foundation
- [ ] Create directory structure
- [ ] Set up core types/interfaces
- [ ] Implement base class

## Phase 2: Core Logic
- [ ] Implement main algorithm
- [ ] Add validation
- [ ] Write unit tests

## Phase 3: Integration
- [ ] Wire into existing system
- [ ] Update configuration
- [ ] Manual testing / verification
```

## Rules

- Create the plan BEFORE starting implementation, not after.
- Update the plan file after each significant step.
- If the user provides a plan, follow it and update it; do not create a duplicate.
- Delete the plan file only when the user confirms the work is complete and the file is no longer needed.
