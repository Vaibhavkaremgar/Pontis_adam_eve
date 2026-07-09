import { useState, useEffect } from 'react';

export const MOCK_CANDIDATES = [
  {
     id: 'INT-2024-0042',
    date: '2024-07-05',
    duration: '48 min',
    status: 'Completed',
    candidate: { name: 'Sarah Mitchell', role: 'Senior Frontend Engineer', experience: '6 years', location: 'San Francisco, CA'},
    overallScore: 8.2,
    recommendation: 'Strong Hire',
    videoUrl: 'https://www.w3schools.com/html/mov_bbb.mp4',
    scoreJustification: 'Sarah demonstrated exceptional technical depth across all evaluated dimensions. Her problem-solving approach was methodical and well-articulated, and she showed strong command of modern frontend architecture patterns. Communication was clear and confident throughout. Minor gaps in distributed systems knowledge are easily offset by her strong coding skills and cultural alignment.',
    scores: [
      { label: 'Communication',       score: 8.5 },
      { label: 'Technical Knowledge', score: 8.0 },
      { label: 'Problem Solving',     score: 8.8 },
      { label: 'Coding Skills',       score: 9.0 },
      { label: 'Experience',          score: 7.5 },
      { label: 'Adaptability',        score: 8.2 },
      { label: 'Culture Fit',         score: 8.6 },
      { label: 'Overall Impression',  score: 8.2 },
    ],
    analysisText: `Sarah communicated with notable clarity throughout the interview, structuring her responses using the STAR method and asking thoughtful clarifying questions that reflected genuine engagement with each problem. Her technical foundation is strong — she demonstrated confident command of React, TypeScript, and modern build tooling, accurately explaining virtual DOM reconciliation, hooks lifecycle, and state management trade-offs, though her exposure to backend and distributed systems is limited and would benefit from further development in the role.\n\nDuring the live coding segment, Sarah approached each problem methodically, decomposing requirements, anticipating edge cases, and iterating based on feedback. Her code was clean, well-named, and production-quality with minimal prompting, and she proactively considered error handling and performance implications. Her six years of frontend experience — particularly her work on large-scale SPAs and design system contributions — maps directly to the responsibilities of this role.\n\nSarah adapted well when requirements shifted mid-exercise, remaining composed under ambiguity and receptive to course corrections, which speaks to a strong learning mindset. Her values align closely with the team's collaborative, ownership-driven culture, and she expressed genuine enthusiasm for the product mission. Overall, she is a standout candidate who combines deep technical skill with effective communication and cultural alignment, and would be positioned to contribute meaningfully from day one.`,
  },
  {
    id: 'INT-2024-0043',
    date: '2024-07-08',
    duration: '52 min',
    status: 'Completed',
    candidate: { name: 'James Okafor', role: 'Backend Engineer', experience: '4 years', location: 'Austin, TX' },
    overallScore: 7.4,
    recommendation: 'Hire',
    videoUrl: 'https://www.w3schools.com/html/movie.mp4',
    scoreJustification: 'James showed solid backend fundamentals and a clear understanding of API design and database optimisation. His system design answers were reasonable though lacked depth on distributed patterns. Strong communication and a collaborative attitude make him a good team fit.',
    scores: [
      { label: 'Communication',       score: 7.5 },
      { label: 'Technical Knowledge', score: 7.8 },
      { label: 'Problem Solving',     score: 7.2 },
      { label: 'Coding Skills',       score: 7.6 },
      { label: 'Experience',          score: 7.0 },
      { label: 'Adaptability',        score: 7.8 },
      { label: 'Culture Fit',         score: 7.4 },
      { label: 'Overall Impression',  score: 7.4 },
    ],
    analysisText: `James demonstrated solid backend fundamentals throughout the interview, communicating his thought process clearly and confidently. He showed a good grasp of RESTful API design, database indexing strategies, and Node.js event-loop behaviour, though his answers on distributed caching and message queues were surface-level and would need strengthening for senior-level work.\n\nIn the system design exercise, James produced a reasonable architecture for a URL shortener, correctly identifying the need for a hash collision strategy and a read-heavy caching layer. His coding was functional and logically sound, though he occasionally skipped input validation steps until prompted. Four years of experience building microservices at a mid-size SaaS company translates well to the role's core responsibilities.\n\nJames responded positively to feedback during the session and showed genuine curiosity when the interviewer introduced constraints he hadn't considered, suggesting a healthy learning mindset. He expressed strong interest in the team's engineering culture and open-source contributions. Overall a solid candidate who would ramp up effectively with some mentorship on distributed systems depth.`,
  },
  {
    id: 'INT-2024-0044',
    date: '2024-07-10',
    duration: '41 min',
    status: 'Under Review',
    candidate: { name: 'Priya Nair', role: 'Full Stack Engineer', experience: '3 years', location: 'New York, NY'},
    overallScore: 6.1,
    recommendation: 'Hold',
    videoUrl: 'https://www.w3schools.com/html/mov_bbb.mp4',
    scoreJustification: 'Priya demonstrated foundational frontend skills but showed gaps in performance optimisation and backend knowledge. Her enthusiasm and collaborative mindset are positives, but the current skill level does not yet meet the bar for this role. Recommend revisiting in a future cycle.',
    scores: [
      { label: 'Communication',       score: 6.5 },
      { label: 'Technical Knowledge', score: 5.8 },
      { label: 'Problem Solving',     score: 6.0 },
      { label: 'Coding Skills',       score: 6.2 },
      { label: 'Experience',          score: 5.5 },
      { label: 'Adaptability',        score: 6.8 },
      { label: 'Culture Fit',         score: 6.5 },
      { label: 'Overall Impression',  score: 6.1 },
    ],
    analysisText: `Priya presented herself professionally and communicated with reasonable clarity, though she occasionally struggled to structure longer answers and needed follow-up prompts to reach the core of her reasoning. Her frontend knowledge covers the basics of React and CSS well, but she showed limited familiarity with performance optimisation techniques such as memoisation, code splitting, and lazy loading.\n\nThe live coding task revealed that Priya can produce working solutions for straightforward problems, but she found it difficult to reason about time complexity and did not proactively consider edge cases. Her three years of experience span a mix of freelance and one full-time role, which has given her broad exposure but less depth in any single area. The gap between her current skill level and the role's expectations is noticeable, particularly on the backend side where she had limited hands-on experience.\n\nPriya showed enthusiasm and a willingness to learn, and her collaborative attitude would fit the team culture. However, at this stage she would benefit from another year of focused full-stack development before being a strong fit for this position. A hold is recommended with the option to revisit in a future hiring cycle.`,
  },
];

export function useInterviewList() {
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setLoading(false), 700);
    return () => clearTimeout(t);
  }, []);
  return { candidates: MOCK_CANDIDATES, loading };
}

export function useInterviewById(id) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true);
    const t = setTimeout(() => {
      setData(MOCK_CANDIDATES.find(c => c.id === id) ?? null);
      setLoading(false);
    }, 700);
    return () => clearTimeout(t);
  }, [id]);
  return { data, loading };
}
