"""Standalone test for skill detection — pure Python, no Gemini call,
no pipeline run needed."""

from skills import detect_relevant_skills, load_skill_content

# Simulate a scanned Flask codebase
fake_context = {
    "file_full_contents": {
        "app.py": "from flask import Flask, request\napp = Flask(__name__)\n"
    }
}

matched = detect_relevant_skills(fake_context)
print("Matched skills:", matched)
print("\n--- Loaded skill content ---\n")
print(load_skill_content(matched))

# Also confirm a non-Flask codebase correctly matches nothing
fake_non_flask = {"file_full_contents": {"main.js": "import React from 'react';"}}
print("\nNon-Flask codebase matched:", detect_relevant_skills(fake_non_flask))
