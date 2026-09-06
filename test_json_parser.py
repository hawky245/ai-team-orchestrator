#!/usr/bin/env python3
"""Test the shared JSON parser."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.utils.json_parser import _extract_and_parse_json, JSONParseError

def test_basic_json():
    """Test basic JSON parsing."""
    text = '{"key": "value", "number": 42}'
    result = _extract_and_parse_json(text)
    assert result == {"key": "value", "number": 42}
    print("PASS Basic JSON test passed")

def test_markdown_fences():
    """Test JSON extraction from markdown code fences."""
    text = '''
Here is the JSON:
```json
{
  "task_id": "t1",
  "description": "Create database schema",
  "dependencies": [],
  "context": {}
}
```
'''
    result = _extract_and_parse_json(text)
    assert result["task_id"] == "t1"
    assert result["description"] == "Create database schema"
    print("PASS Markdown fences test passed")

def test_multiline_sql():
    """Test JSON with multiline SQL in raw_output."""
    text = '''{
  "raw_output": "CREATE TABLE users (\\n  id INT PRIMARY KEY,\\n  name VARCHAR(100)\\n);\\n\\nCREATE TABLE posts (\\n  id INT PRIMARY KEY,\\n  user_id INT,\\n  content TEXT\\n);",
  "artifacts": []
}'''
    result = _extract_and_parse_json(text)
    assert "CREATE TABLE users" in result["raw_output"]
    assert "\n" in result["raw_output"]  # Contains newline characters
    print("PASS Multiline SQL test passed")

def test_nested_objects():
    """Test JSON with nested objects."""
    text = '''{
  "summary": "Database design",
  "tasks": [
    {
      "task_id": "t1",
      "description": "Create users table",
      "dependencies": [],
      "context": {"schema": "public"}
    }
  ],
  "execution_order": "sequential"
}'''
    result = _extract_and_parse_json(text)
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["task_id"] == "t1"
    print("PASS Nested objects test passed")

def test_empty_response():
    """Test empty response raises error."""
    try:
        _extract_and_parse_json("")
        assert False, "Should have raised JSONParseError"
    except JSONParseError:
        print("PASS Empty response test passed")

def test_malformed_json():
    """Test malformed JSON raises error."""
    try:
        _extract_and_parse_json('{"key": "value"')
        assert False, "Should have raised JSONParseError"
    except JSONParseError:
        print("PASS Malformed JSON test passed")

def test_no_braces():
    """Test text with no braces raises error."""
    try:
        _extract_and_parse_json("Just plain text")
        assert False, "Should have raised JSONParseError"
    except JSONParseError:
        print("PASS No braces test passed")

if __name__ == "__main__":
    print("Running JSON parser tests...")
    test_basic_json()
    test_markdown_fences()
    test_multiline_sql()
    test_nested_objects()
    test_empty_response()
    test_malformed_json()
    test_no_braces()
    print("\nAll tests passed!")