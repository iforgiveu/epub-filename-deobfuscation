import os
import zipfile
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import re
import urllib.parse
import shutil

class EPUBFilenameCleaner:
    def __init__(self, input_dir=".", output_dir="./cleaned_epubs"):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        
        # XML命名空间
        self.ns = {
            'opf': 'http://www.idpf.org/2007/opf',
            'dc': 'http://purl.org/dc/elements/1.1/',
            'xhtml': 'http://www.w3.org/1999/xhtml',
            'svg': 'http://www.w3.org/2000/svg',
            'epub': 'http://www.idpf.org/2007/ops'
        }
        
    def find_epub_files(self):
        """递归查找所有EPUB文件"""
        epub_files = []
        for root, dirs, files in os.walk(self.input_dir):
            for file in files:
                if file.lower().endswith('.epub'):
                    full_path = Path(root) / file
                    epub_files.append(full_path)
        return epub_files
    
    def is_path_obfuscated(self, path_str):
        """检查路径是否被混淆"""
        try:
            decoded = urllib.parse.unquote(path_str)
            special_chars = sum(1 for c in decoded if c in '*?|:<>"\\/')
            has_url_encoded = any(x in path_str for x in ['%2A', '%3F', '%7C', '%3A', '%2F'])
            return special_chars > 2 or has_url_encoded
        except:
            return False
    
    def get_clean_filename_from_id(self, item_id, media_type=None):
        """从item的id生成规范文件名"""
        # 提取文件名（可能已经包含扩展名）
        id_path = Path(item_id)
        name = id_path.stem
        ext = id_path.suffix
        
        # 如果没有扩展名，根据media-type添加
        if not ext and media_type:
            ext_map = {
                'application/xhtml+xml': '.xhtml',
                'application/xml': '.xml',
                'text/css': '.css',
                'image/jpeg': '.jpg',
                'image/png': '.png',
                'image/gif': '.gif',
                'image/svg+xml': '.svg',
                'font/ttf': '.ttf',
                'font/otf': '.otf',
                'font/woff': '.woff',
                'font/woff2': '.woff2',
                'application/javascript': '.js',
                'text/javascript': '.js'
            }
            ext = ext_map.get(media_type, '')
        
        # 清理非法字符
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        name = name.strip('.')
        
        if not name:
            return None
        
        return f"{name}{ext}"
    
    def find_opf_file(self, extract_dir):
        """找到OPF文件"""
        container_path = extract_dir / "META-INF" / "container.xml"
        if container_path.exists():
            try:
                tree = ET.parse(container_path)
                root = tree.getroot()
                for elem in root.iter():
                    if 'rootfile' in elem.tag and 'full-path' in elem.attrib:
                        opf_path = elem.attrib['full-path']
                        return extract_dir / opf_path
            except:
                pass
        
        opf_files = list(extract_dir.rglob("*.opf"))
        if opf_files:
            return opf_files[0]
        
        return None
    
    def collect_rename_mapping(self, manifest, opf_dir):
        """收集需要重命名的文件映射 {旧路径: 新路径}"""
        rename_map = {}
        
        for item in manifest.findall('.//*[@href]'):
            href = item.get('href', '')
            item_id = item.get('id', '')
            media_type = item.get('media-type', '')
            
            # 检查是否需要修复
            if self.is_path_obfuscated(href):
                old_path = Path(href)
                old_full_path = opf_dir / old_path
                
                # 使用id生成新文件名
                new_filename = self.get_clean_filename_from_id(item_id, media_type)
                
                if new_filename:
                    new_path = old_path.parent / new_filename
                    
                    # 确保新路径和旧路径不同
                    if str(old_path) != str(new_path):
                        rename_map[str(old_path)] = str(new_path)
                        # print(f"    计划重命名: {old_path} -> {new_path} (基于id: {item_id})")
        
        return rename_map
    
    def rename_actual_files(self, extract_dir, rename_map):
        """重命名实际的物理文件"""
        renamed_files = []
        print(f"    ✓ 文件路径前缀测试: {extract_dir}")
        for old_rel_path, new_rel_path in rename_map.items():
            old_rel_path = urllib.parse.unquote(old_rel_path)
            old_full = extract_dir /'OEBPS'/ old_rel_path
            new_full = extract_dir /'OEBPS'/ new_rel_path
            # print(f"    ✓ 需完整路径测试: {old_full}~~~{new_full}")
            if old_full.exists():
                try:
                    # 确保目标目录存在
                    new_full.parent.mkdir(parents=True, exist_ok=True)
                    # 执行重命名
                    old_full.rename(new_full)
                    renamed_files.append((old_rel_path, new_rel_path))
                    print(f"    ✓ 已重命名文件: {old_rel_path} -> {new_rel_path}")
                except Exception as e:
                    print(f"    ✗ 重命名失败 {old_rel_path}: {str(e)}")
            else:
                print(f"    ✗ 文件不存在: {old_rel_path}")
                # 尝试查找文件（可能路径大小写问题）
                search_pattern = old_full.name
                found = list(extract_dir.rglob(search_pattern))
                if found:
                    pass
                    # print(f"      找到相似文件: {found[0].relative_to(extract_dir)}")
                    # 可以在这里添加处理逻辑
        
        return renamed_files
    
    def update_opf_references(self, opf_path, rename_map):
        """更新OPF文件中的href引用"""
        if not opf_path or not opf_path.exists():
            return
        
        try:
            # 读取文件内容
            with open(opf_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            modified = False
            # 更新所有href引用
            for old_path, new_path in rename_map.items():
                if old_path in content:
                    content = content.replace(f'href="{old_path}"', f'href="{new_path}"')
                    content = content.replace(f"href='{old_path}'", f"href='{new_path}'")
                    modified = True
                    # print(f"    OPF更新引用: {old_path} -> {new_path}")
            
            if modified:
                with open(opf_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                    
        except Exception as e:
            print(f"  更新OPF时出错: {str(e)}")
    
    def update_file_references(self, file_path, rename_map, file_type="xhtml"):
        """更新文件中的所有引用"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            modified = False
            
            # 匹配各种引用模式
            patterns = [
                (r'(src|href|poster|data)\s*=\s*"([^"]*)"', 2),
                (r"(src|href|poster|data)\s*=\s*'([^']*)'", 2),
                (r'@import\s+["\']([^"\']*)["\']', 1),
                (r'url\(["\']?([^"\'()]*)["\']?\)', 1),
            ]
            
            for pattern, group_idx in patterns:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    old_ref = match.group(group_idx)
                    
                    # 检查这个引用是否需要更新
                    for old_path, new_path in rename_map.items():
                        old_filename = Path(old_path).name
                        new_filename = Path(new_path).name
                        
                        # 如果引用中包含旧文件名
                        if old_filename in old_ref:
                            new_ref = old_ref.replace(old_filename, new_filename)
                            if new_ref != old_ref:
                                content = content.replace(match.group(0), 
                                    match.group(0).replace(old_ref, new_ref))
                                modified = True
                                # print(f"    {file_type.upper()}修复: {old_ref} -> {new_ref}")
                                break
            
            if modified:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                    
        except Exception as e:
            # 某些文件可能不是文本文件，跳过
            pass
    
    def update_all_references(self, extract_dir, rename_map):
        """更新所有文件中的引用"""
        # 更新XHTML/HTML文件
        html_files = list(extract_dir.rglob("*.xhtml")) + list(extract_dir.rglob("*.html")) + list(extract_dir.rglob("*.htm"))
        for file_path in html_files:
            self.update_file_references(file_path, rename_map, "xhtml")
        
        # 更新CSS文件
        css_files = list(extract_dir.rglob("*.css"))
        for file_path in css_files:
            self.update_file_references(file_path, rename_map, "css")
        
        # 更新NCX文件
        ncx_files = list(extract_dir.rglob("*.ncx"))
        for file_path in ncx_files:
            self.update_file_references(file_path, rename_map, "ncx")
        
        # 更新SMIL文件
        smil_files = list(extract_dir.rglob("*.smil"))
        for file_path in smil_files:
            self.update_file_references(file_path, rename_map, "smil")
    
    def detect_files(self, extract_dir):
        """自执行函数：检测第一个ttf文件和第一个webp文件的实际路径"""
        print("\n=== 文件检测结果 ===")
        
        # 查找第一个 ttf 文件
        ttf_files = list(extract_dir.rglob("*.ttf"))
        if ttf_files:
            first_ttf = ttf_files[0]
            print(f"第一个 TTF 文件: {first_ttf}")
            print(f"相对路径: {first_ttf.relative_to(extract_dir)}")
        else:
            print("未找到 TTF 文件")
        
        # 查找第一个 webp 文件
        webp_files = list(extract_dir.rglob("*.webp"))
        if webp_files:
            first_webp = webp_files[0]
            print(f"第一个 WEBP 文件: {first_webp}")
            print(f"相对路径: {first_webp.relative_to(extract_dir)}")
        else:
            print("未找到 WEBP 文件")
        
        print("=" * 40)
    
    def remove_zhangyue_expansion(self, epub_path):
        """处理单个EPUB文件"""
        try:
            relative_path = self.get_relative_path(epub_path)
            output_path = self.output_dir / relative_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # print(f"\n处理: {epub_path}")
            
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                extract_dir = temp_path / "extracted"
                
                # 1. 解压EPUB
                # print("  解压EPUB文件...")
                with zipfile.ZipFile(epub_path, 'r') as zf:
                    zf.extractall(extract_dir)
                
                # 调用文件检测函数
                self.detect_files(extract_dir)
                
                # 2. 删除zhangyue-expansion.xml
                target_file = extract_dir / "META-INF" / "zhangyue-expansion.xml"
                if target_file.exists():
                    target_file.unlink()
                    # print("  已删除: META-INF/zhangyue-expansion.xml")
                # else:
                    # print("  未找到: META-INF/zhangyue-expansion.xml")
                
                # 3. 找到OPF文件
                opf_path = self.find_opf_file(extract_dir)
                if not opf_path:
                    # print("  警告: 未找到OPF文件")
                    # 直接打包原文件
                    self.repack_epub(extract_dir, output_path)
                    return True
                
                # print(f"  找到OPF: {opf_path.relative_to(extract_dir)}")
                
                # 4. 解析OPF，获取manifest
                try:
                    tree = ET.parse(opf_path)
                    root = tree.getroot()
                    
                    # 查找manifest
                    manifest = None
                    for elem in root.iter():
                        if 'manifest' in elem.tag:
                            manifest = elem
                            break
                    
                    if manifest:
                        # 5. 收集需要重命名的文件映射
                        rename_map = self.collect_rename_mapping(manifest, opf_path.parent)
                        
                        if rename_map:
                            # print(f"\n  发现 {len(rename_map)} 个混淆文件需要重命名")
                            
                            # 6. 重命名实际的物理文件
                            # print("\n  重命名实际文件:")
                            self.rename_actual_files(extract_dir, rename_map)
                            
                            # 7. 更新OPF文件中的引用
                            # print("\n  更新OPF引用:")
                            self.update_opf_references(opf_path, rename_map)
                            
                            # 8. 更新所有其他文件中的引用
                            # print("\n  更新其他文件引用:")
                            self.update_all_references(extract_dir, rename_map)
                        # else:
                            # print("  未发现需要修复的混淆文件")
                    # else:
                        # print("  未找到manifest")
                        
                except ET.ParseError as e:
                    print(f"  OPF解析错误: {str(e)}")
                except Exception as e:
                    print(f"  处理OPF时出错: {str(e)}")
                
                # 9. 重新打包
                # print("\n  重新打包EPUB...")
                self.repack_epub(extract_dir, output_path)
                
            # print(f"✓ 完成: {output_path}")
            return True
            
        except Exception as e:
            print(f"✗ 处理失败 {epub_path}: {str(e)}")
            return False
    
    def repack_epub(self, source_dir, output_path):
        """重新打包EPUB"""
        source_path = Path(source_dir)
        
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in source_path.rglob('*'):
                if file_path.is_file():
                    arcname = file_path.relative_to(source_path)
                    zf.write(file_path, arcname)
    
    def get_relative_path(self, file_path):
        """获取相对路径"""
        try:
            return file_path.relative_to(self.input_dir)
        except ValueError:
            return file_path.name
    
    def process_all(self):
        """处理所有EPUB文件"""
        epub_files = self.find_epub_files()
        
        if not epub_files:
            print("未找到EPUB文件")
            return
        
        print(f"找到 {len(epub_files)} 个EPUB文件")
        print("=" * 60)
        
        success_count = 0
        for i, epub_path in enumerate(epub_files, 1):
            print(f"\n[{i}/{len(epub_files)}] ", end="")
            if self.remove_zhangyue_expansion(epub_path):
                success_count += 1
        
        print(f"\n{'='*60}")
        print(f"处理完成: {success_count}/{len(epub_files)} 个文件成功")

def main():
    """主函数"""
    print("EPUB文件处理工具")
    print("=" * 60)
    print("功能:")
    print("  1. 删除 META-INF/zhangyue-expansion.xml")
    print("  2. 修复混淆的文件名（基于item的id）")
    print("  3. 重命名实际文件")
    print("  4. 更新所有引用（OPF、XHTML、CSS、NCX等）")
    print("=" * 60)
    
    input_dir = input("输入目录 (默认为当前目录): ").strip() or "."
    output_dir = input("输出目录 (默认为 ./cleaned_epubs): ").strip() or "./cleaned_epubs"
    
    cleaner = EPUBFilenameCleaner(input_dir, output_dir)
    cleaner.process_all()

if __name__ == "__main__":
    main()