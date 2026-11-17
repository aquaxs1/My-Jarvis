// Runs before the page paints: tells the stylesheet that scripts are on, so
// content that fades in on scroll starts hidden instead of flashing first.
document.documentElement.classList.add('js');
