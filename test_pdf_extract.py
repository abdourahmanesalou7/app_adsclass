import os
import fitz  # PyMuPDF

# Tester l'extraction sur un PDF existant
uploads_dir = 'uploads'
pdf_files = [f for f in os.listdir(uploads_dir) if f.endswith('.pdf')]

print(f"=== PDFS TROUVES: {len(pdf_files)} ===")
for pdf_file in pdf_files[:3]:  # Tester les 3 premiers
    pdf_path = os.path.join(uploads_dir, pdf_file)
    print(f"\n📄 Fichier: {pdf_file}")
    print(f"   Chemin: {pdf_path}")
    print(f"   Existe: {os.path.exists(pdf_path)}")
    
    try:
        doc = fitz.open(pdf_path)
        print(f"   Pages: {len(doc)}")
        
        # Extraire le texte de la première page
        if len(doc) > 0:
            text = doc[0].get_text()
            preview = text[:500].replace('\n', ' ')
            print(f"   Texte (preview): {preview}...")
        doc.close()
    except Exception as e:
        print(f"   Erreur: {e}")

print("\n✅ Test terminé!")

