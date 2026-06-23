        assert "- spam" in diff
        assert "+ eggs" in diff

    def test_text_skipping(self):
        lines = callequal("a" * 50 + "spam", "a" * 50 + "eggs")
        assert "Skipping" in lines[1]
