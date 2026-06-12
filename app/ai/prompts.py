"""Prompt templates for the AI matching and recommendation pipeline."""

JOB_MATCH_PROMPT = """You are a senior technical recruiter. Evaluate how well a candidate's resume matches a job posting.

Analyse the following dimensions and return a **valid JSON object** (no markdown fences).

## Dimensions to analyse

1. **ATS keyword match** — The ATS analysis found these matched/missing keywords: {ats_matched} / {ats_missing}.
2. **Skills match** — Which of the candidate's skills appear in the job requirements? Which required skills are missing?
3. **Experience match** — How does the candidate's work history align with the role's seniority and domain?
4. **Project relevance** — Which of the candidate's projects are most relevant to the job's responsibilities?
5. **Technology overlap** — What specific technologies, tools, or platforms overlap?

## Output schema (MUST be valid JSON)

```json
{{
  "score": <float between 0.0 and 1.0>,
  "reasoning": "<free-text explanation>",
  "matched_skills": ["<skill>", ...],
  "missing_skills": ["<skill>", ...],
  "recommended_projects": ["<project>", ...]
}}
```

## Job Posting

**Title:** {title}
**Company:** {company}
**Location:** {location}

**Description:**
{description}

## Resume Profile

**Summary:** {resume_summary}

**Skills:** {skills}

**Experience:** {experience}

**Projects (name — technologies):**
{projects}

**Education:** {education}

**Certifications:** {certifications}

Evaluate honestly. A score of 0.5 means a reasonable but not outstanding match. Reserve scores above 0.85 for roles where the candidate is nearly a perfect fit."""


PROJECT_SELECTION_PROMPT = """You are a career coach. Select the most relevant projects from a candidate's portfolio for a specific job application.

Return a **valid JSON object** (no markdown fences).

## Output schema

```json
{{
  "selected_projects": ["<project_name>", ...],
  "reasoning": "<why these projects were selected>"
}}
```

## Job Posting

**Title:** {title}
**Company:** {company}
**Description:**
{description}

## Available Projects

{projects}

Select projects whose technologies, domain, or complexity best align with the job requirements."""


APPLICATION_RECOMMENDATION_PROMPT = """You are a job search strategist. Given a job match analysis and the candidate's preferences, recommend whether to apply.

Return a **valid JSON object** (no markdown fences).

## Output schema

```json
{{
  "apply": <true|false>,
  "priority": "<HIGH|MEDIUM|LOW|REJECT>",
  "explanation": "<reasoning>"
}}
```

## Job Match

**Score:** {score}/1.0
**Matched skills:** {matched_skills}
**Missing skills:** {missing_skills}
**Reasoning:** {reasoning}

## Candidate Preferences

**Location:** Bangalore, India
**Open to:** Full Time, Internship, Contract, Part Time, Hourly Jobs
**Preferred locations:** Bangalore, Hybrid Bangalore, Remote India, International Remote
**Preferred roles:** Frontend Developer, React Developer, JavaScript Developer, Software Engineer, Full Stack Developer, Customer Support, Technical Support, Operations Associate, Any legitimate laptop-based remote work

Be honest. Do not recommend applying for roles that are clearly outside the candidate's skill set or preferences."""
