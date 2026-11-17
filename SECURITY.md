# Security policy

OptimizationBench runs untrusted, agent-written code inside task containers and
evaluates it with a separate judge. Please report any of the following
privately rather than in a public issue:

- a way for agent-written code to escape a task or judge container;
- a way to influence a score without a genuine, correct speedup (for
  example, detecting the evaluation inputs, bypassing a correctness gate or
  tampering with timing);
- exposure of credentials or private data in this repository or its images.

Use GitHub's private vulnerability reporting ("Report a vulnerability" on
the repository's Security tab). Include the affected Task, the commit or
image digest, and steps to reproduce. We aim to acknowledge reports within
five business days.
