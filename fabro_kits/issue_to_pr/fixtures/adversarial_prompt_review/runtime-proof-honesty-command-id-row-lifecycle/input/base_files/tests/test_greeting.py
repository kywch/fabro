
class GreetingTest(unittest.TestCase):
    def test_greeting_uses_comma(self):
        self.assertEqual(greeting("Ada"), 'hello Ada')
