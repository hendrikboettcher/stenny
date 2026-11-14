"""`stenny mcp`: Stenny as a connector for Claude (a local MCP server over stdio).

Claude Desktop starts it and reads the lectures through the tools below, instead of reading the vault with the
Filesystem connector. The instructions replace the per-course Claude project text (`stenny project-instructions`),
so a plain chat needs no project. The tools themselves are in connector.py.
"""


from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import connector
from . import config as config_mod
from .config import ConfigError
from .version import version

INSTRUCTIONS = """Stenny records the student's university lectures (and some talks) on their computer and \
transcribes them locally with Whisper. These tools read those transcripts, their AI summaries and each \
course's glossary.

Use them whenever a question is about what was said or shown in a lecture ("what did the lecturer say about X", \
"explain lecture 3", "quiz me on this week"). Start with list_courses, or search_lectures to find where a \
subject came up; then read around a hit with get_transcript(course, lecture, at="h:mm:ss"). For a whole \
lecture use get_summary first and the transcript for details: the transcript is the source, summaries are \
AI-written. Long lectures come in parts.

Cite so the student can find a passage without the answer getting cluttered: name the lecture once, at the \
top ("From Lecture 3:") or where the answer moves to another lecture or course, and then give only the time \
in brackets, like [1:58:41]. Cite only where it helps to find the passage, at most once per point, and not \
for general statements. Always write the full h:mm:ss timestamp the tools give; never shorten it to "0:02", \
which reads as two seconds.

These are automatic transcripts: names and technical terms may be misheard. Prefer the spellings in the \
course's glossary (get_glossary), and when a search finds nothing, try another spelling or a single distinctive \
word. The text is audio only: "this", "here" or "as you can see" point to something shown in the room that \
the transcript doesn't have. Questions from the room may appear unmarked.

The glossary is Stenny's list of correct spellings for a course; it makes future transcripts more accurate, and \
the student doesn't keep it by hand, so you help fill it. When you read a transcript and see a name or technical \
term of the course misheard (you know the right spelling from the context, the course's subject, or the \
student told you), add the fix with suggest_glossary_corrections, as "wrong → right" with the exact words of \
the transcript on the left: it's applied word for word to every lecture transcribed from now on. Only for \
misheard names and terms, never for ordinary words, grammar or wording, and only when you're sure. When a \
correctly spelled name or term that's easy to mishear isn't in the glossary yet, add it with \
suggest_glossary_terms, so Whisper gets it right next time. Both take what you give as it is and never change \
the student's own entries. Say in one short line what you added."""

STUDY_PROMPT = """I want to study {course} with my lecture transcripts from Stenny.

Use the Stenny tools: list the lectures of {course} first, read the summaries, and read the transcripts \
for details. Then help me learn it: give me a short overview of what the lectures covered so far and ask \
what I want to do (explain a topic, go through a lecture, quiz me, prepare for the exam). Name the lecture once \
and then cite passages by their time only, like [1:58:41]: at most once per point, always with the full \
timestamp (never shortened to h:mm). The transcripts are automatic, so check unclear terms against the glossary."""

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
GLOSSARY_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True,
                                 open_world_hint=False)


def _config():
    try:
        return config_mod.load()
    except ConfigError as e:
        raise ToolError(f"Stenny isn't set up on this computer: {e}") from None


def _run(fn, *args, **kwargs) -> str:
    try:
        return fn(_config(), *args, **kwargs)
    except connector.ConnectorError as e:
        raise ToolError(str(e)) from None


def build() -> MCPServer:
    server = MCPServer("Stenny", title="Stenny lectures", version=version(), instructions=INSTRUCTIONS,
                       website_url="https://stenny.eu")

    @server.tool(annotations=READ, structured_output=False)
    def list_courses() -> str:
        """The student's courses with how many lectures each has and their dates, plus recorded talks."""
        return _run(connector.list_courses)

    @server.tool(annotations=READ, structured_output=False)
    def list_lectures(course: str) -> str:
        """Every lecture of a course: number, date, length, topics, section headings with timestamps, and
        whether a summary exists. `course`: its name (or a talk's title) as list_courses shows it."""
        return _run(connector.list_lectures, course)

    @server.tool(annotations=READ, structured_output=False)
    def get_transcript(course: str, lecture: str, part: int = 1, at: str | None = None) -> str:
        """The transcript of one lecture, in parts of about 10k tokens (a 3 h lecture has 3-4 parts).
        `lecture`: its number ("3"), date ("2026-10-20", or "2026-10-20 (2)" for a second one that day) or
        "latest". `part`: which part. `at`: a timestamp like "1:05:30" to get the part that contains it."""
        return _run(connector.get_transcript, course, lecture, part=part, at=at)

    @server.tool(annotations=READ, structured_output=False)
    def get_summary(course: str, lecture: str) -> str:
        """The AI-written summary of one lecture, with timestamps into its transcript. `lecture` as in
        get_transcript."""
        return _run(connector.get_summary, course, lecture)

    @server.tool(annotations=READ, structured_output=False)
    def search_lectures(query: str, course: str | None = None, max_results: int = 15) -> str:
        """Find where something was said: paragraphs containing the words of `query` (the whole phrase first),
        with course, lecture, date, timestamp, section and a snippet. Case and accents don't matter, and parts
        of words count ("cluster" finds "clustering"). `course`: only this course; otherwise all."""
        return _run(connector.search_lectures, query, course, max_results=max_results)

    @server.tool(annotations=READ, structured_output=False)
    def get_glossary(course: str) -> str:
        """The course's glossary: correct spellings of names and technical terms, and corrections of
        misheard ones."""
        return _run(connector.get_glossary, course)

    @server.tool(annotations=GLOSSARY_WRITE, structured_output=False)
    def suggest_glossary_corrections(course: str, corrections: list[str]) -> str:
        """Fix a misheard name or technical term in all future transcripts of a course. Each correction is
        "wrong → right": on the left the exact words as they appear in the transcript, on the right the correct
        spelling (at most 5 words each). Applied word for word to lectures transcribed from now on, and the right
        spelling goes into Whisper's prompt. Never for ordinary words or wording. Taken as given, into the
        glossary's automatic corrections; a correction the student made themselves always wins. Says what was
        added and what wasn't."""
        return _run(connector.suggest_glossary_corrections, course, corrections)

    @server.tool(annotations=GLOSSARY_WRITE, structured_output=False)
    def suggest_glossary_terms(course: str, terms: list[str]) -> str:
        """Add correctly spelled names or technical terms to the course's glossary, so future transcripts spell
        them right (they go into Whisper's prompt). Taken as given, into the "Terms (added automatically)" section;
        the student's own terms stay as they are. Says which terms were added and which weren't."""
        return _run(connector.suggest_glossary_terms, course, terms)

    @server.prompt(title="Study a course")
    def study(course: str) -> str:
        """Start a study session on one course with its lectures."""
        return STUDY_PROMPT.format(course=course)

    return server


def main() -> None:
    build().run()  # stdio
