from grok_search.sources import (
    extract_sources_from_openai_response,
    split_answer_and_sources,
)


def test_split_answer_extracts_inline_markdown_citations_without_removing_answer():
    text = (
        "OpenAI posted product updates [[1]](https://openai.com/news/) "
        "and release notes [[2]](https://help.openai.com/en/articles/6825453-chatgpt-release-notes)."
    )

    answer, sources = split_answer_and_sources(text)

    assert answer == text
    assert sources == [
        {"url": "https://openai.com/news/"},
        {"url": "https://help.openai.com/en/articles/6825453-chatgpt-release-notes"},
    ]


def test_extract_sources_from_openai_response_reads_search_sources_and_annotations():
    payload = {
        "search_sources": [
            {
                "url": "https://openai.com/news/",
                "title": "OpenAI News",
                "type": "web",
            }
        ],
        "choices": [
            {
                "message": {
                    "content": "Answer [[1]](https://openai.com/news/)",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url_citation": {
                                "url": "https://help.openai.com/en/articles/6825453-chatgpt-release-notes",
                                "title": "ChatGPT release notes",
                            },
                        }
                    ],
                }
            }
        ],
    }

    sources = extract_sources_from_openai_response(payload)

    assert sources == [
        {
            "url": "https://openai.com/news/",
            "title": "OpenAI News",
            "type": "web",
        },
        {
            "url": "https://help.openai.com/en/articles/6825453-chatgpt-release-notes",
            "title": "ChatGPT release notes",
        },
    ]
