from app.services.ocr import clean_ocr_markdown

def test_only_unresolved_empty_crop_markers_are_removed():
    text = r"![](abc_img.jpg) $x=-2$ ![график](plot.png) ![](https://example.com/p.png)"
    result = clean_ocr_markdown(text)
    assert result == r"$x=-2$ ![график](plot.png) ![](https://example.com/p.png)"
