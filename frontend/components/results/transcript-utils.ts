export type TranscriptSegment = {
  timestamp: string | null;
  speaker: string;
  role: "interviewer" | "candidate" | "system";
  text: string;
};

const TIMESTAMP_PATTERN = /^\s*(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\]|\((\d{1,2}:\d{2}(?::\d{2})?)\)|(\d{1,2}:\d{2}(?::\d{2})?))\s*[-]?\s*/;
const SPEAKER_PATTERN = /^([A-Za-z][A-Za-z .\-_/&]+?)\s*:\s*(.+)$/;

function classifySpeaker(speaker: string): TranscriptSegment["role"] {
  const normalized = speaker.trim().toLowerCase();
  if (/(candidate|interviewee|applicant)/.test(normalized)) return "candidate";
  if (/(recruiter|interviewer|hiring manager|adam|manager|panel)/.test(normalized)) return "interviewer";
  return "system";
}

function normalizeSpeaker(speaker: string): string {
  const normalized = speaker.trim();
  if (!normalized) return "System";
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

export function parseTranscriptSegments(transcript: string): TranscriptSegment[] {
  const lines = String(transcript || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const segments: TranscriptSegment[] = [];
  for (const line of lines) {
    const timestampMatch = line.match(TIMESTAMP_PATTERN);
    const timestamp = timestampMatch?.[1] || timestampMatch?.[2] || timestampMatch?.[3] || null;
    const remainder = timestampMatch ? line.slice(timestampMatch[0].length).trim() : line;
    const speakerMatch = remainder.match(SPEAKER_PATTERN);
    const speaker = speakerMatch ? normalizeSpeaker(speakerMatch[1] || "System") : "System";
    const text = speakerMatch ? speakerMatch[2] || "" : remainder;
    segments.push({
      timestamp,
      speaker,
      role: classifySpeaker(speaker),
      text: text.trim(),
    });
  }

  return segments;
}
