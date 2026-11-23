# App Icons and Splash Screens Guide

This document provides instructions for creating app icons and splash screens for the Fyvio Streaming PWA.

## Required Icon Sizes

### PWA Icons
Place these files in the `public/` directory:

- **pwa-192x192.png** - 192x192 pixels (Android adaptive icon)
- **pwa-512x512.png** - 512x512 pixels (iOS/Android high-res icon)
- **favicon.ico** - 16x16, 32x32, 48x48 pixels (Browser favicon)
- **apple-touch-icon.png** - 180x180 pixels (iOS home screen icon)
- **masked-icon.svg** - SVG format (Android adaptive icon mask)

### Splash Screen
- **splash-screen.png** - 1242x2208 pixels (iPhone X/11/12/13/14 Pro Max)
- **splash-screen-tablet.png** - 2048x2732 pixels (iPad Pro)

## Design Guidelines

### Brand Colors
- Primary: #E50914 (Netflix Red)
- Secondary: #B20710 (Darker Red)
- Background: #0B0B0C (Dark Background)
- Text: #FFFFFF (White)

### Icon Design
- Use the "Fyvio" logo or a play button icon
- Keep it simple and recognizable at small sizes
- Use the primary red color (#E50914)
- Add subtle gradient from #E50914 to #B20710
- Ensure good contrast on both light and dark backgrounds

### Splash Screen Design
- Center the app logo
- Use gradient background from #0B0B0C to #1a1a1a
- Add subtle animation or pattern
- Include app name "Fyvio Streaming" below logo
- Ensure it looks good on both portrait and landscape

## Tools for Generating Icons

### Online Tools
1. **PWA Asset Generator** - https://www.pwabuilder.com/imageGenerator
2. **Favicon.io** - https://favicon.io/
3. **RealFaviconGenerator** - https://realfavicongenerator.net/

### Command Line Tools
```bash
# Using ImageMagick
convert icon.png -resize 192x192 pwa-192x192.png
convert icon.png -resize 512x512 pwa-512x512.png
convert icon.png -resize 180x180 apple-touch-icon.png

# Using sharp (Node.js)
npx sharp icon.png -resize 192x192 pwa-192x192.png
npx sharp icon.png -resize 512x512 pwa-512x512.png
```

### Design Software
- **Figma** - Export multiple sizes at once
- **Adobe Illustrator** - Vector icons, export as PNG
- **Canva** - Pre-sized templates for app icons
- **Sketch** - Mac design tool with icon export

## Quick Start with PWA Asset Generator

1. Go to https://www.pwabuilder.com/imageGenerator
2. Upload your base icon (minimum 512x512 PNG)
3. Select "Fyvio Streaming" as app name
4. Choose #E50914 as theme color
5. Download the generated assets
6. Extract and place files in the `public/` directory

## Manual Creation Steps

### Step 1: Create Base Icon
1. Create a 1024x1024 canvas
2. Design your icon using brand colors
3. Save as `icon-base.png`

### Step 2: Generate Required Sizes
```bash
# Using ImageMagick
convert icon-base.png -resize 192x192 pwa-192x192.png
convert icon-base.png -resize 512x512 pwa-512x512.png
convert icon-base.png -resize 180x180 apple-touch-icon.png
convert icon-base.png -resize 16x16 favicon-16x16.png
convert icon-base.png -resize 32x32 favicon-32x32.png
convert icon-base.png -resize 48x48 favicon-48x48.png

# Create favicon.ico from multiple sizes
convert favicon-16x16.png favicon-32x32.png favicon-48x48.png favicon.ico
```

### Step 3: Create SVG Mask
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <circle cx="50" cy="50" r="50" fill="black"/>
</svg>
```
Save as `masked-icon.svg`

### Step 4: Create Splash Screens
```bash
# iPhone splash screen
convert icon-base.png -resize 200x200 temp-logo.png
convert -size 1242x2208 xc:'#0B0B0C' splash-bg.png
composite -gravity center temp-logo.png splash-bg.png splash-screen.png

# Add text to splash screen
convert splash-screen.png -pointsize 48 -fill white -gravity center \
  -annotate +0+150 'Fyvio Streaming' splash-screen.png
```

## Testing Icons

### Test on Different Devices
1. **Chrome DevTools** - Device toolbar, select different devices
2. **Lighthouse** - PWA audit to verify icons
3. **Real Devices** - Test on actual Android and iOS devices

### Verify Manifest
Check that the manifest includes all required icons:
```json
{
  "icons": [
    {
      "src": "pwa-192x192.png",
      "sizes": "192x192",
      "type": "image/png"
    },
    {
      "src": "pwa-512x512.png",
      "sizes": "512x512",
      "type": "image/png"
    }
  ]
}
```

## Icon Best Practices

1. **Simplicity** - Keep design simple, avoid complex details
2. **Contrast** - Ensure good contrast at all sizes
3. **Consistency** - Use consistent branding across all icons
4. **Testing** - Test icons at actual sizes before finalizing
5. **Vector First** - Start with vector design for scalability
6. **Platform Guidelines** - Follow Apple and Android design guidelines

## Troubleshooting

### Icons Not Showing
- Clear browser cache
- Verify file paths in manifest
- Check file permissions
- Ensure files are in `public/` directory

### Splash Screen Issues
- Verify dimensions match device
- Check file size (keep under 500KB)
- Test on actual device
- Ensure proper manifest configuration

### Favicon Not Updating
- Clear browser cache completely
- Check favicon.ico file format
- Verify HTML link tags
- Test in incognito mode

## Additional Resources

- [Web App Manifest](https://developer.mozilla.org/en-US/docs/Web/Manifest)
- [PWA Best Practices](https://web.dev/progressive-web-apps/)
- [Apple Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/app-icons)
- [Android Adaptive Icons](https://developer.android.com/guide/practices/ui_guidelines/icon_design_adaptive)
