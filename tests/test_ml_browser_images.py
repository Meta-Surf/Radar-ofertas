import unittest

import mercadolivre_auto as auto


class BrowserImagePolicyTests(unittest.TestCase):
    def test_allows_only_mlstatic_https_images(self):
        self.assertTrue(auto.browser_image_allowed('https://http2.mlstatic.com/D_NQ_NP_TEST-O.webp'))
        self.assertTrue(auto.browser_image_allowed('https://http2.mlstatic.com/D_NQ_NP_TEST-O.webp?x=1'))
        self.assertFalse(auto.browser_image_allowed('https://evil.test/image.webp'))
        self.assertFalse(auto.browser_image_allowed('http://http2.mlstatic.com/image.webp'))
        self.assertFalse(auto.browser_image_allowed('data:image/gif;base64,AAAA'))


if __name__ == '__main__':
    unittest.main()
