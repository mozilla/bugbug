When you cite a source file in your comment, write it as an inline Markdown link built from the `{searchfox_placeholder}` placeholder, which is expanded into a revision-pinned Searchfox URL when the comment is recorded:

    [Document.cpp]({searchfox_placeholder}/dom/base/Document.cpp#1234)

Write the placeholder literally, follow it with the repo-relative path and an optional `#line` anchor, use the file name alone as the link text, and only cite a path you confirmed exists. A path not in the checkout is unwrapped to plain text.
