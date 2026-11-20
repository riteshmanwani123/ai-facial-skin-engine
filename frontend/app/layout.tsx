import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "CLARITY by Dr. Tanya | AI Facial Skin Engine",
  description: "AI Facial Skin Engine",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Playfair+Display:wght@500;600;700&family=Lora:ital,wght@0,400;0,500;0,600;1,400&display=swap"
        />
        <link
          rel="stylesheet"
          href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
